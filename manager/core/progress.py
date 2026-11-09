"""Agent-reported progress — what a run says about its own work.

A stream of tool events proves *activity*: a file was read, a command ran.
It does not say what the run has accomplished, what it is attempting now,
what remains, or what it is stuck on — and only the run knows that. So a
run is given one narrow way to say it, and the board keeps what it says
apart from everything the board observed itself.

The contract, end to end:

- **One channel per run.** A launch whose adapter declares
  `progress-report` in its `capabilities.json` is handed the path of
  `core/bench-progress` (AGENT_PROGRESS) and a run-scoped token
  (BOARD_PROGRESS_TOKEN). The adapter renders that one executable as an
  allow-rule and nothing else: no shell, no task-file write, no lifecycle
  action and no network beyond this board's loopback port comes with it.
  An adapter that does not declare the capability is handed neither, the
  prompt says nothing about reporting, and the board says *reporting
  unavailable* rather than *no progress*.
- **Identity is checked here.** A report names its run and carries that
  run's token; only a hash of the token is kept, on disk and in memory.
  A report for a run this board did not open, with the wrong token, or for
  a run that has ended is refused — and a refused report against a real
  run is counted on that run's channel health, so a spoof attempt or a
  broken shim is visible rather than silent.
- **What a report may hold.** A stage, what it is doing now, milestones
  done, what remains, a blocker, a short narrative, and references to
  commits or to bench's own evidence records — each bounded, control
  characters refused, credential-shaped text masked, and no free-form link
  or path accepted as a reference. Every report is a whole statement: a
  field it omits is *not reported*, never carried over from an earlier one
  and never inferred.
- **Ordering, replay and noise.** Each report has an id (the shim makes
  one and reuses it on retry), so a repeated request is answered as the
  duplicate it is. Each has the time the run made it; a late arrival is
  filed into the history where it belongs and never displaces a newer
  current update. Reports closer together than PROGRESS_MIN_GAP are
  coalesced into the previous history entry, and a run is capped at
  MAX_REPORTS accepted reports. The milestone checklist is accumulated on
  the record itself, so coalescing and the history cap never take a
  reported milestone off it.
- **Terminal states supersede.** When the run ends the record is closed
  with the outcome the board observed — finished, failed, declined, held
  — and the last update becomes history, dated, rather than a "doing now"
  claim about a process that is gone. A board that restarts closes every
  run it was tracking as `unobserved`: it cannot know how they ended.
- **Agent-reported is a trust label, not a result.** Nothing here reads a
  narrative as evidence. A reference to an evidence record is resolved
  against `verify`'s own record — its outcome *now*, check, scope, commit
  and freshness — so "tests passed" in prose sits beside what the runner
  actually recorded, and cannot be promoted by it.

Records are machine-local (`local/state/agent/progress/<agent id>.json`,
beside the run logs), for
the reason manifests and evidence are: they belong to runs on this board.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

import config
import profiles
import state
import verify

VERSION = 1

# The intents a run may report progress from. `refine` has no shell, so it
# has nothing to run the shim with, and it is never handed one.
INTENTS = ("work", "act-pr", "review")

# What an adapter declares in capabilities.json to be handed the channel.
CAPABILITY = "progress-report"

# Field limits. Generous enough for a sentence or two, tight enough that a
# report cannot become a transcript.
LIMITS = {"stage": 80, "doing": 280, "blocked": 400, "narrative": 800}
LIST_LIMITS = {"done": (12, 200), "remaining": (12, 200)}
MAX_REFS = 8
MAX_BODY = 16 * 1024
MAX_REPORTS = 200
HISTORY_CAP = 50
# The milestone checklist is kept apart from the history, so neither
# coalescing nor the history cap can take a reported milestone off it. It
# is bounded too: the earliest MILESTONE_CAP by first-seen time are kept,
# and the rest are counted rather than shown.
MILESTONE_CAP = 100
SEEN_CAP = 400
CLOCK_SKEW = 5.0
# A refused report is counted at once, but the page is told about refusals
# at most once per this many seconds per run (with one trailing notice), so
# a burst of bad reports cannot make every open page reload per request.
REJECT_NOTIFY_GAP = 5.0

FIELDS = ("stage", "doing", "done", "remaining", "blocked", "narrative")
ALLOWED_KEYS = {"v", "agent", "token", "id", "at", "refs", "criteria", *FIELDS}
# Acceptance criteria a run says it has implemented (card 81): ids only,
# the shape `taskfiles.CRITERION_ID_RE` writes. A claim, never a result —
# `acceptance` shows it as *reported* and nothing it says ticks a
# criterion. At most MAX_CRITERIA per report.
MAX_CRITERIA = 20
_CRITERION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")

_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")
_AGENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
# Control characters other than a newline. A tab becomes a space first.
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f  ‪-‮⁦-⁩]")
# Lines the board parses out of an agent's closing report. A progress
# update travels by HTTP and is never parsed for them, but a report that
# *reads* as a closing marker is refused anyway: an update must not look
# like the run's ending in any place a person or a parser could meet it.
_MARKER = re.compile(r"^\s*(NOT READY:|PR REVIEW:|RELEVANCE REVIEW:|ADDRESSED:|REFINEMENT\b)",
                     re.M)

OUTCOMES = ("finished", "failed", "declined", "held", "unobserved")

_LOCK = threading.Lock()
RUNS: dict[str, dict] = {}            # agent id -> record (token hash, never the token)
_NOTIFY: dict[str, dict] = {}         # agent id -> {"last": monotonic, "timer": Timer}


class Refused(ValueError):
    """A report the board would not record, with the HTTP status a caller
    should answer it with and a short code a script can branch on."""

    def __init__(self, status: int, code: str, why: str):
        super().__init__(why)
        self.status = status
        self.code = code
        self.why = why


# ── the channel ────────────────────────────────────────────────────────


def shim() -> Path:
    """The executable a run reports through. Beside the rest of core, so it
    travels with an update and is found by absolute path from any
    worktree, exactly as `bench-verify` is."""
    return config.CORE / "bench-progress"


def channel(intent: str, adapter: dict | None = None) -> dict:
    """Whether a launch of this intent, through this board's adapter, can
    report progress — and if not, why not, in words the page can show.

    Read from the adapter's own declaration; an adapter override that has
    never heard of progress declares nothing, is handed nothing, and keeps
    working exactly as before."""
    if intent not in INTENTS:
        return {"state": "unavailable",
                "why": f"the '{intent}' intent has no shell to report with"}
    if adapter is None:
        adapter = profiles.adapter_identity()
    strength = (adapter.get("capabilities") or {}).get(CAPABILITY)
    if not strength:
        return {"state": "unavailable",
                "why": (f"the {adapter.get('name') or 'configured'} adapter does "
                        f"not declare '{CAPABILITY}', so this run was given no "
                        f"way to report progress — its tool activity is all "
                        f"the board can show")}
    if not shim().is_file():
        return {"state": "unavailable",
                "why": f"{shim().name} is missing from core — reinstall bench"}
    return {"state": "available", "enforcement": strength,
            "why": "the run may run bench-progress, and nothing else comes "
                   "with that grant"}


def prompt_block(intent: str, available: bool) -> str:
    """What the run is told about reporting. Empty when the channel is not
    available, so an adapter without it renders the prompt it always did."""
    if not available or intent not in INTENTS:
        return ""
    path = shim()
    return "\n".join([
        "**Progress updates.** The board shows a person what you have done, "
        "what you are doing now, what remains and what blocks you — but "
        "only what you tell it. Your tool calls already show activity; an "
        "update says what that activity amounts to. Report through this "
        "one command (it is granted to you; nothing else comes with it):",
        "",
        f"    {path} --stage \"<short stage>\" --doing \"<what you are doing now>\" \\",
        "        --done \"<milestone reached>\" --remaining \"<what is left>\" \\",
        "        [--blocked \"<what stops you>\"] [--commit <sha>] [--evidence <id>] \\",
        "        \"<one or two sentences a teammate can read>\"",
        "",
        "Send one at the start (your approach and first stage), then at "
        "meaningful milestones, when the approach changes, when you are "
        "blocked, and when significant verification starts and ends — not "
        "after every read or edit. Each update is a whole statement: say "
        "what changed, what is happening now and what remains, rather than "
        "naming the last tool you used. `--done` and `--remaining` may be "
        "repeated. Write public status, not your private reasoning.",
        "",
        "An update is labelled agent-reported. Saying tests passed is not "
        "evidence: pass `--evidence <id>` with the id bench-verify printed, "
        "or `--commit <sha>` for a commit you made, and the board shows the "
        "record beside your words. An update never ends the run and is not "
        "your closing report — that is still your final reply. If the "
        "command fails, carry on with the work; a reporting failure must "
        "never stop it.",
        "",
        "When the card's acceptance criteria carry ids (`<!-- ac:<id> -->` "
        "on the criterion's line), `--criterion <id>` (repeatable) says you "
        "implemented that criterion. The card shows it as reported and "
        "awaiting confirmation; only a person accepts a criterion, so never "
        "claim one you did not implement, and never edit the ids.",
    ])


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _path(agent_id: str) -> Path | None:
    if not _AGENT_RE.match(agent_id or "") or ".." in agent_id:
        return None
    return _dir() / f"{agent_id}.json"


def _dir() -> Path:
    """Where the records live: beside the agent logs unless configured,
    so a relocated run log takes what the run reported along with it."""
    return config.PROGRESS_DIR or (config.AGENT_DIR / "progress")


def _store(record: dict) -> None:
    path = _path(record["agent"])
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def open_run(agent_id: str, task: str, intent: str, chan: dict) -> str | None:
    """Open a run's progress record and return its token — None when the
    channel is unavailable, which is then recorded as the run's channel
    state so the page can say *reporting unavailable* in words."""
    now = time.time()
    token = secrets.token_urlsafe(24) if chan.get("state") == "available" else None
    record = {
        "v": VERSION, "agent": agent_id, "task": task, "intent": intent,
        "tokenHash": _hash(token) if token else None,
        "channel": {"state": chan.get("state"), "why": chan.get("why")},
        "opened": now, "closed": None, "outcome": None,
        "current": None, "history": [], "seen": [],
        "milestones": [], "milestonesOmitted": [],
        "accepted": 0, "coalesced": 0, "duplicates": 0, "rejected": 0,
        "lastRejection": None, "lastReport": None, "lastReceived": None,
        "lastReminded": None, "seq": 0,
    }
    with _LOCK:
        RUNS[agent_id] = record
        _store(record)
    return token


def close_run(agent_id: str, outcome: str) -> None:
    """The run ended, as the board observed it. Idempotent, and a later
    call may refine the outcome (a clean exit that turns out to have been a
    decline)."""
    if outcome not in OUTCOMES:
        outcome = "failed"
    with _LOCK:
        record = RUNS.get(agent_id)
        if record is None:
            return
        record["closed"] = record.get("closed") or time.time()
        record["outcome"] = outcome
        _store(record)
    state.broadcast({"type": "progress", "agent": agent_id})


def load() -> int:
    """Read every record back after a restart. A run this board was still
    tracking is closed `unobserved`: its process may have outlived the
    board, but the board that launched it is gone and nothing will observe
    its ending, so "doing now" would be a claim nobody can keep true."""
    found = 0
    if not _dir().is_dir():
        return 0
    for path in _dir().glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not _AGENT_RE.match(str(record.get("agent") or "")):
            continue
        changed = False
        if not isinstance(record.get("milestones"), list):
            # a record written before milestones were kept apart: rebuild
            # them from the history it has, which is all it can offer
            record["milestones"], record["milestonesOmitted"] = [], []
            for entry in sorted(record.get("history") or [], key=lambda e: e["at"]):
                _remember(record, entry)
            changed = True
        if not record.get("closed"):
            record["closed"] = time.time()
            record["outcome"] = "unobserved"
            changed = True
        with _LOCK:
            RUNS[record["agent"]] = record
            if changed:
                _store(record)
        found += 1
    return found


# ── a report ───────────────────────────────────────────────────────────


def _text(name: str, value, cap: int) -> str:
    if not isinstance(value, str):
        raise Refused(400, "schema", f"'{name}' must be text")
    value = value.replace("\t", " ").replace("\r\n", "\n").replace("\r", "\n")
    if _CONTROL.search(value):
        raise Refused(400, "unsafe", f"'{name}' holds control or bidirectional "
                                     f"characters — send plain text")
    value = "\n".join(line.rstrip() for line in value.strip().split("\n"))
    if len(value) > cap:
        raise Refused(400, "too-long", f"'{name}' is {len(value)} characters; "
                                       f"the limit is {cap} — say less")
    if _MARKER.search(value):
        raise Refused(400, "marker", f"'{name}' holds a line that reads as a "
                                     f"closing-report marker; a progress update "
                                     f"is never the run's ending")
    return verify.scrub(value)


def _refs(raw, task: str) -> tuple[list[dict], list[str]]:
    """References to things the board itself can read: a commit by sha, or
    an evidence record by id that belongs to this card. Anything else is
    dropped with a reason — never a URL, never a path."""
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        raise Refused(400, "schema", "'refs' must be a list")
    if len(raw) > MAX_REFS:
        raise Refused(400, "too-long", f"at most {MAX_REFS} references")
    out, dropped = [], []
    for item in raw:
        if not isinstance(item, dict) or len(item) != 1:
            dropped.append("a reference is one of {\"commit\": sha} or {\"evidence\": id}")
            continue
        (kind, value), = item.items()
        value = str(value or "").strip()
        if kind == "commit" and _SHA_RE.match(value.lower()):
            out.append({"kind": "commit", "id": value.lower()})
        elif kind == "evidence" and verify.is_evidence_id(value):
            record = verify.read(value)
            if record is None or record.get("task") != task:
                dropped.append(f"evidence {value} is not a record for this card")
                continue
            out.append({"kind": "evidence", "id": value})
        else:
            dropped.append(f"{kind!r} is not a reference the board can read")
    return out, dropped


def _criteria(raw) -> list[str]:
    """Criterion ids a run reports implemented: validated as identifiers,
    de-duplicated, order kept. Whether the card holds them is the card's
    business (`acceptance` reads them against the brief and ignores an id
    the card does not have), so this refuses only what is not an id."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise Refused(400, "schema", "'criteria' must be a list of criterion ids")
    if len(raw) > MAX_CRITERIA:
        raise Refused(400, "too-long", f"at most {MAX_CRITERIA} criteria per report")
    out: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if not _CRITERION_RE.match(value):
            raise Refused(400, "schema", f"{value[:40]!r} is not a criterion id "
                                         f"(lowercase letters, digits and -)")
        if value not in out:
            out.append(value)
    return out


def _claim(record: dict, entry: dict) -> None:
    """Fold the criteria one entry reports into the run's own list, kept on
    the record (like the milestones) so the history cap cannot drop one.
    Each id keeps the newest report that named it and the evidence that
    report referred to — a *proposal* of support, resolved by `acceptance`
    against bench's own record, never taken as a result."""
    ids = entry.get("criteria") or []
    if not ids:
        return
    kept = record.setdefault("criteriaReported", {})
    evidence = [r["id"] for r in entry.get("refs") or [] if r["kind"] == "evidence"]
    for cid in ids:
        known = kept.get(cid)
        if known and known["at"] > entry["at"]:
            continue
        kept[cid] = {"at": entry["at"], "report": entry["id"], "evidence": evidence}


def _reject(record: dict | None, code: str, status: int, why: str) -> Refused:
    if record is not None:
        with _LOCK:
            record["rejected"] = record.get("rejected", 0) + 1
            record["lastRejection"] = {"at": time.time(), "code": code, "why": why}
            _store(record)
        _notify_health(record["agent"])
    return Refused(status, code, why)


def _notify_health(agent_id: str) -> None:
    """Tell the page a run's channel health changed — at most once per
    REJECT_NOTIFY_GAP, with one trailing notice so the last refusal of a
    burst is shown too. The counts themselves are recorded on every
    refusal; only the broadcast is bounded."""
    gap = REJECT_NOTIFY_GAP
    now = time.monotonic()
    with _LOCK:
        slot = _NOTIFY.setdefault(agent_id, {"last": None, "timer": None})
        if slot["last"] is None or now - slot["last"] >= gap:
            slot["last"] = now
            send = True
        else:
            send = False
            if slot["timer"] is None:
                timer = threading.Timer(gap - (now - slot["last"]), _trailing, (agent_id,))
                timer.daemon = True
                slot["timer"] = timer
                timer.start()
    if send:
        state.broadcast({"type": "progress", "agent": agent_id})


def _trailing(agent_id: str) -> None:
    with _LOCK:
        slot = _NOTIFY.get(agent_id)
        if slot is not None:
            slot["timer"] = None
            slot["last"] = time.monotonic()
    state.broadcast({"type": "progress", "agent": agent_id})


def _remember(record: dict, entry: dict) -> None:
    """Fold one entry's milestones into the run's checklist. Called under
    the lock for every accepted report — recorded, coalesced or filed — so
    the checklist is independent of what the history later drops. A text
    already listed keeps one row, dated by the earliest report that named
    it; the earliest MILESTONE_CAP are kept and the rest counted."""
    done = entry.get("done") or []
    if not done:
        return
    kept = record.setdefault("milestones", [])
    omitted = record.setdefault("milestonesOmitted", [])
    index = {m["text"]: m for m in kept}
    for text in done:
        known = index.get(text)
        if known is not None:
            if entry["at"] < known["at"]:
                known["at"] = entry["at"]
            continue
        digest = _hash(text)[:16]
        if digest in omitted:
            continue
        item = {"text": text, "at": entry["at"]}
        kept.append(item)
        index[text] = item
    kept.sort(key=lambda m: m["at"])          # stable: report order within one entry
    while len(kept) > MILESTONE_CAP:
        omitted.append(_hash(kept.pop()["text"])[:16])
    del omitted[:-SEEN_CAP]


def report(raw: dict) -> dict:
    """Validate one report and record it. Returns what happened —
    `recorded`, `coalesced`, `duplicate` or `filed` (an older report, kept
    in the history without displacing a newer current one) — and raises
    Refused with a status and a reason for anything it would not keep."""
    if not isinstance(raw, dict):
        raise Refused(400, "schema", "a report is a JSON object")
    agent_id = str(raw.get("agent") or "")
    with _LOCK:
        record = RUNS.get(agent_id)
    if record is None:
        raise Refused(404, "unknown-run",
                      "this board opened no progress channel for that run")
    token = raw.get("token")
    if not record.get("tokenHash"):
        raise _reject(record, "unavailable", 409,
                      "this run was launched without a progress channel")
    if not isinstance(token, str) or not hmac.compare_digest(
            _hash(token), record["tokenHash"]):
        raise _reject(record, "identity", 403,
                      "the report's token does not belong to this run — a run "
                      "may only report its own progress")
    if record.get("closed"):
        raise _reject(record, "closed", 409,
                      f"this run has ended ({record.get('outcome')}); its "
                      f"progress is history now")
    if raw.get("v", VERSION) != VERSION:
        raise _reject(record, "schema", 400, f"unknown report version {raw.get('v')!r}")
    unknown = sorted(set(raw) - ALLOWED_KEYS)
    if unknown:
        raise _reject(record, "schema", 400,
                      f"unknown field{'s' if len(unknown) > 1 else ''}: "
                      f"{', '.join(unknown)}")
    report_id = raw.get("id")
    if not isinstance(report_id, str) or not _ID_RE.match(report_id):
        raise _reject(record, "schema", 400, "a report needs an id (8–80 of "
                                             "letters, digits, . _ : -)")
    with _LOCK:
        if report_id in record["seen"]:
            record["duplicates"] = record.get("duplicates", 0) + 1
            _store(record)
            return {"result": "duplicate", "id": report_id}

    try:
        body: dict = {}
        for name, cap in LIMITS.items():
            if raw.get(name) is not None:
                value = _text(name, raw[name], cap)
                if value:
                    body[name] = value
        for name, (count, cap) in LIST_LIMITS.items():
            items = raw.get(name)
            if items is None:
                continue
            if isinstance(items, str):
                items = [items]
            if not isinstance(items, list):
                raise Refused(400, "schema", f"'{name}' must be a list of text")
            if len(items) > count:
                raise Refused(400, "too-long", f"'{name}' holds {len(items)} "
                                               f"items; the limit is {count}")
            cleaned = [v for v in (_text(name, i, cap) for i in items) if v]
            if cleaned:
                body[name] = cleaned
        criteria = _criteria(raw.get("criteria"))
        if criteria:
            body["criteria"] = criteria
        if not any(body.get(f) for f in (*FIELDS, "criteria")):
            raise Refused(400, "empty", "a report must say something — a stage, "
                                        "what you are doing, done, remaining, a "
                                        "blocker or a narrative")
        refs, dropped = _refs(raw.get("refs"), record["task"])
    except Refused as refused:
        raise _reject(record, refused.code, refused.status, refused.why)

    now = time.time()
    at = raw.get("at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        at = now
    # a run's clock is the run's: held between when the run opened and now
    at = max(record["opened"] - CLOCK_SKEW, min(float(at), now + CLOCK_SKEW))
    entry = {"id": report_id, "at": at, "received": now, **body}
    if refs:
        entry["refs"] = refs

    with _LOCK:
        if record.get("closed"):
            raise Refused(409, "closed", "this run has ended")
        if record["accepted"] >= MAX_REPORTS:
            record["rejected"] += 1
            record["lastRejection"] = {"at": now, "code": "rate-limited",
                                       "why": f"more than {MAX_REPORTS} reports"}
            _store(record)
            raise Refused(429, "rate-limited",
                          f"this run has sent {MAX_REPORTS} updates; the board "
                          f"records no more — report at milestones, not per step")
        record["seq"] += 1
        entry["seq"] = record["seq"]
        record["seen"].append(report_id)
        del record["seen"][:-SEEN_CAP]
        record["accepted"] += 1
        record["lastReceived"] = now
        current = record.get("current")
        newest = current is None or at >= current["at"]
        history = record["history"]
        if newest:
            gap = config.PROGRESS_MIN_GAP
            if (history and gap and history[-1]["id"] == (current or {}).get("id")
                    and at - history[-1]["at"] < gap
                    and not entry.get("blocked") and not history[-1].get("blocked")):
                # Coalesced: the newer statement replaces the one just
                # before it in the timeline. A blocker is never coalesced
                # away, in either direction — it is the update a person
                # must not miss.
                entry["coalesced"] = history[-1].get("coalesced", 0) + 1
                history[-1] = entry
                record["coalesced"] += 1
                result = "coalesced"
            else:
                history.append(entry)
                result = "recorded"
            record["current"] = entry
            record["lastReport"] = at
        else:
            history.append(entry)
            history.sort(key=lambda e: (e["at"], e.get("seq", 0)))
            result = "filed"
        _remember(record, entry)
        _claim(record, entry)
        del history[:-HISTORY_CAP]
        _store(record)
    state.broadcast({"type": "progress", "agent": agent_id})
    out = {"result": result, "id": report_id}
    if dropped:
        out["dropped"] = dropped
    return out


# ── reminders ──────────────────────────────────────────────────────────


def reminder(agent_id: str | None, now: float | None = None) -> str | None:
    """A sentence to hand the run at its next tool boundary, or None.

    Asked by the event ingest for a tool event of an open run with a
    channel. It never interrupts anything and costs no model call of the
    board's own: the adapter's bridge passes it back as context on a tool
    result the run was going to read anyway. Once per interval at most."""
    interval = config.PROGRESS_REMIND
    if not agent_id or not interval:
        return None
    now = now or time.time()
    with _LOCK:
        record = RUNS.get(agent_id)
        if (record is None or record.get("closed")
                or (record.get("channel") or {}).get("state") != "available"):
            return None
        quiet_since = max(record.get("lastReceived") or 0, record["opened"],
                          record.get("lastReminded") or 0)
        if now - quiet_since < interval:
            return None
        record["lastReminded"] = now
        _store(record)
        said = record.get("lastReceived")
    minutes = int((now - (said or record["opened"])) // 60)
    if said:
        lead = f"Your last progress update was {minutes} min ago."
    else:
        lead = f"You have not sent a progress update in the {minutes} min since this run started."
    return (f"[bench] {lead} When you reach a natural point — not in the "
            f"middle of a check — send one with {shim()}: what is done, what "
            f"you are doing now, what remains, and anything blocking you. "
            f"Do not stop or end your turn for this.")


# ── what the board shows ───────────────────────────────────────────────


def _evidence(ref: dict) -> dict:
    """A reference to an evidence record, resolved against the record as it
    reads *now*. The agent's words are never an input here."""
    record = verify.read(ref["id"])
    if record is None:
        return {**ref, "found": False}
    now = verify.outcome_now(record)
    source = record.get("source") or {}
    return {**ref, "found": True, "check": record.get("check"),
            "scope": record.get("scope"), "outcome": now.get("outcome"),
            "recorded": record.get("outcome"), "reason": now.get("reason"),
            "commit": (source.get("commit") or "")[:12] or None,
            "dirty": bool(source.get("dirty")),
            "ended": record.get("ended"), "source": "bench"}


def _public_entry(entry: dict, resolve: bool) -> dict:
    out = {k: entry[k] for k in ("id", "at", "received", "seq", "coalesced",
                                 "criteria", *FIELDS) if k in entry}
    out["source"] = "agent-reported"
    refs = entry.get("refs") or []
    if refs:
        out["refs"] = [(_evidence(r) if resolve and r["kind"] == "evidence" else dict(r))
                       for r in refs]
    return out


def milestones(record: dict) -> list[dict]:
    """Every milestone the run has reported done, in the order first
    reported — the checklist that survives a later update not repeating
    it, a coalesced burst and the history cap alike, because it is kept on
    the record rather than read back out of the history. Exact repeats are
    one milestone; nothing is inferred."""
    kept = record.get("milestones")
    if isinstance(kept, list):
        return [{"text": m["text"], "at": m["at"]} for m in kept]
    # a legacy record not yet loaded through load(): the history is all
    # there is, so read it as the history always was
    legacy = {"milestones": [], "milestonesOmitted": []}
    for entry in sorted(record.get("history") or [], key=lambda e: e["at"]):
        _remember(legacy, entry)
    return legacy["milestones"]


def public(agent_id: str, full: bool = False) -> dict | None:
    """One run's progress as the page may show it. Never the token hash,
    never the seen-id list."""
    with _LOCK:
        record = RUNS.get(agent_id)
        record = json.loads(json.dumps(record)) if record else None
    if record is None:
        return None
    out = {
        "agent": record["agent"], "task": record["task"],
        "intent": record.get("intent"),
        "channel": record.get("channel"),
        "opened": record.get("opened"), "closed": record.get("closed"),
        "outcome": record.get("outcome"),
        "active": not record.get("closed"),
        "current": (_public_entry(record["current"], resolve=True)
                    if record.get("current") else None),
        "lastReport": record.get("lastReport"),
        "lastReceived": record.get("lastReceived"),
        "counts": {k: record.get(k, 0) for k in
                   ("accepted", "coalesced", "duplicates", "rejected")},
        "lastRejection": record.get("lastRejection"),
        "updates": len(record.get("history") or []),
        "milestones": milestones(record),
        "milestonesOmitted": len(record.get("milestonesOmitted") or []),
        "criteriaReported": record.get("criteriaReported") or {},
    }
    if full:
        out["history"] = [_public_entry(e, resolve=True)
                          for e in record.get("history") or []]
    return out


def claims_for(task: str) -> dict:
    """Every criterion any run on this card has reported implemented, the
    newest report per criterion winning — with the run that said it. Read
    by `acceptance`, which shows these as claims and never as results."""
    out: dict = {}
    with _LOCK:
        for agent_id, record in RUNS.items():
            if record.get("task") != task:
                continue
            for cid, claim in (record.get("criteriaReported") or {}).items():
                if cid not in out or claim["at"] > out[cid]["at"]:
                    out[cid] = {**claim, "agent": agent_id,
                                "intent": record.get("intent"),
                                "active": not record.get("closed")}
    return out


def board_public(tasks: set[str] | None = None) -> dict:
    """The compact reading the state payload carries: per card, its most
    recent run's progress (open or closed), without the history. A new run
    on the card supersedes the older one; a run never shows another run's
    current update."""
    with _LOCK:
        latest: dict[str, tuple[float, str]] = {}
        for agent_id, record in RUNS.items():
            task = record.get("task")
            if tasks is not None and task not in tasks:
                continue
            opened = record.get("opened") or 0
            if task not in latest or opened > latest[task][0]:
                latest[task] = (opened, agent_id)
    out = {}
    for task, (_, agent_id) in latest.items():
        found = public(agent_id)
        if found:
            out[task] = found
    return out
