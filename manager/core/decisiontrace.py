"""The orchestrator's decision trace: every scheduling pass, kept (task 184).

The scheduler decides something on every pass — to launch, to wait, to
refuse, or that nothing is eligible — and until now only the newest pass
was kept, in memory. This module is the durable record of all of them: a
public decision audit, never a model's private reasoning. A normal pass is
deterministic and asks no model; where a pass asked for a bounded reasoning
job, the job is linked by id and its response stays *agent-reported*.

**One versioned record per pass** (`v: 1`, kind `pass`), written by
`scheduler.run_pass` *after* it decided and acted, holding what it read at
decision time: each candidate's facts, state, rule code and plain reason,
whether it was selected, and what became of the selection — launched,
refused at revalidation, unknown — with the operation key that links it to
the ledger's intent and receipt. A record is never rewritten: what happens
later (a run ending) is the ledger's and the card's, read beside the trace,
never folded back into the decision.

**Identical idle passes are compacted, losslessly.** A pass that acted on
nothing and whose decisions equal the previous record's (same boot, same
segment) is written as a small `repeat` line naming the record it repeats.
The reader folds repeats into their record with the exact count, the
first and last time and every pass id, so nothing is discarded and a
restart always starts a new record.

**Bounded and installation-local.** Records live under
`manager/local/state/orchestration/trace/`, machine-local and never
committed or synced: the shared public history of a card stays its own
`## Orchestration record` (`decisions`). Segments rotate at `SEGMENT_BYTES`
and the newest `KEEP_SEGMENTS` are kept; what was pruned is named in
`meta.json` as the retention boundary. Nothing before the first record is
invented: the trace starts when this feature first ran on this board.

**Recording never steers.** `append` never raises: a failure is counted
and the next record carries it as a recording gap. Reading (`page`,
`entry`, `meta`) writes, launches and reserves nothing.

A stdlib-and-leaves module: it imports `config`, `records` and `redact`.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

import config
import records
import redact

VERSION = 1
KIND = "bench-decision-trace"
SEGMENT_BYTES = 512 * 1024
KEEP_SEGMENTS = 8
TEXT_CAP = 300
MAX_DECISIONS = 80
MAX_LIST = 40
MAX_FACTS = 30
RECORD_CAP = 96 * 1024
PAGE_MAX = 100
REPEAT_IDS_MAX = 500
SEG_RE = re.compile(r"^seg-(\d{12})\.jsonl$")
ID_RE = re.compile(r"^[0-9a-f]{8}-\d{1,12}$")
MARK = "[redacted]"

# What one decision's outcome is called, for the reader and its filters.
OUTCOMES = ("launched", "started", "refused", "unknown", "duplicate", "retry",
            "waiting", "blocked", "held", "working", "not-managed", "recorded",
            "no-action", "skipped", "error")

_LOCK = records.InterprocessLock(lambda: config.STATE / "orchestration" /
                                 "trace.lock")
_MEM = threading.Lock()
HEALTH: dict = {"failures": 0, "since": None, "lastError": None,
                "lastOk": None}
_CACHE: dict = {}


def trace_dir() -> Path:
    return config.STATE / "orchestration" / "trace"


def _meta_path() -> Path:
    return trace_dir() / "meta.json"


def now_iso(epoch: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() if epoch is None else epoch))


# ── bounding what goes in ─────────────────────────────────────────────

def clip(value, cap: int = TEXT_CAP) -> str:
    """A string as a record keeps it: credential-shaped spans masked,
    control characters dropped, at most `cap` characters."""
    text = redact.scrub(str(value if value is not None else ""), MARK)
    text = "".join(ch for ch in text if ch == "\n" or ch >= " ")
    text = text.replace("\n", " ")
    return text if len(text) <= cap else text[:cap - 1] + "…"


def bound(value, depth: int = 0):
    """Any JSON value, bounded: strings clipped and scrubbed, lists and
    dicts capped, nothing deeper than four levels."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return clip(value)
    if depth >= 4:
        return clip(json.dumps(value, default=str))
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out = [bound(v, depth + 1) for v in items[:MAX_LIST]]
        if len(items) > MAX_LIST:
            out.append(f"… {len(items) - MAX_LIST} more")
        return out
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= MAX_FACTS * 2:
                out["…"] = f"{len(value) - i} more"
                break
            out[clip(k, 60)] = bound(v, depth + 1)
        return out
    return clip(value)


def fingerprint(record: dict) -> str:
    """What makes two passes the same decision: everything but when it
    ran, how long it took and its identity."""
    skip = {"id", "seq", "at", "atEpoch", "seconds", "fingerprint",
            "gapBefore", "epoch"}
    body = {k: v for k, v in record.items() if k not in skip}
    return hashlib.sha256(json.dumps(body, sort_keys=True,
                                     default=str).encode()).hexdigest()[:24]


# ── the segments ──────────────────────────────────────────────────────

def _segments() -> list[Path]:
    try:
        names = [p for p in trace_dir().iterdir()
                 if SEG_RE.match(p.name) and p.is_file() and not p.is_symlink()]
    except OSError:
        return []
    return sorted(names, key=lambda p: p.name)


def _read_meta() -> dict:
    return records.read_json(_meta_path(), default={}, expect=dict) or {}


def _tail(path: Path) -> dict | None:
    """The newest whole record in a segment, or None."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 256 * 1024))
            lines = fh.read().splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        try:
            rec = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(rec, dict) and isinstance(rec.get("seq"), int):
            return rec
    return None


def _state() -> dict:
    """The writer's position: the epoch, the last seq, the last record
    (for compaction) and the segment it is in. Read from disk each time,
    so a restart, or a second process, continues where the file says."""
    meta = _read_meta()
    segs = _segments()
    last, seg = None, None
    for path in reversed(segs):
        last = _tail(path)
        if last is not None:
            seg = path
            break
    epoch = meta.get("epoch") if isinstance(meta.get("epoch"), str) else None
    if not epoch and last is not None:
        epoch = str(last.get("epoch") or "") or None
    return {"meta": meta, "segments": segs, "last": last,
            "segment": segs[-1] if segs else None, "lastSegment": seg,
            "epoch": epoch,
            "seq": max(int((last or {}).get("seq") or 0),
                       int(meta.get("prunedThroughSeq") or 0))}


def append(record: dict, *, idle: bool = False) -> dict | None:
    """Write one pass. `idle` says the pass acted on nothing, so it may be
    compacted into the previous record when its decisions are the same.
    Returns what was written, or None — and never raises: a recording
    failure is counted, and the next record says there is a gap."""
    try:
        with _LOCK:
            return _append(record, idle)
    except Exception as exc:  # noqa: BLE001 — recording must never steer
        with _MEM:
            HEALTH["failures"] += 1
            HEALTH["since"] = HEALTH["since"] or now_iso()
            HEALTH["lastError"] = clip(f"{type(exc).__name__}: {exc}", 200)
        return None


def _append(record: dict, idle: bool) -> dict:
    where = trace_dir()
    where.mkdir(parents=True, exist_ok=True)
    st = _state()
    meta = dict(st["meta"])
    epoch = st["epoch"] or secrets.token_hex(4)
    if meta.get("epoch") != epoch:
        meta.update({"v": VERSION, "kind": KIND, "epoch": epoch,
                     "started": meta.get("started") or now_iso()})
        records.write_json(_meta_path(), meta, durable=True)
    seq = st["seq"] + 1
    at = record.get("atEpoch") or time.time()
    full = {k: bound(v) for k, v in record.items()
            if k not in ("seq", "id", "epoch", "v", "kind", "decisions")}
    decisions = list(record.get("decisions") or [])
    full["decisions"] = [bound(d) for d in decisions[:MAX_DECISIONS]]
    if len(decisions) > MAX_DECISIONS:
        full["truncated"] = {"decisions": len(decisions) - MAX_DECISIONS}
    full.update({"v": VERSION, "kind": "pass", "epoch": epoch, "seq": seq,
                 "id": f"{epoch}-{seq}", "atEpoch": at, "at": now_iso(at)})
    full["fingerprint"] = fingerprint(full)
    with _MEM:
        if HEALTH["failures"]:
            full["gapBefore"] = {"failures": HEALTH["failures"],
                                 "since": HEALTH["since"],
                                 "error": HEALTH["lastError"]}
    segment = st["segment"]
    rotate = segment is None or _size(segment) >= SEGMENT_BYTES
    last = st["last"]
    root = None
    if last is not None:
        root = last if last.get("kind") == "pass" else {
            "id": last.get("of"), "fingerprint": last.get("fingerprint"),
            "idle": True, "boot": last.get("boot")}
    line = full
    if (idle and not rotate and root and "gapBefore" not in full
            and root.get("idle") and root.get("fingerprint") == full["fingerprint"]
            and st["lastSegment"] == segment):
        line = {"v": VERSION, "kind": "repeat", "of": root["id"],
                "epoch": epoch, "seq": seq, "id": f"{epoch}-{seq}",
                "atEpoch": at, "at": full["at"], "seconds": full.get("seconds"),
                "fingerprint": full["fingerprint"], "boot": full.get("boot")}
    else:
        full["idle"] = bool(idle)
        text = json.dumps(full, sort_keys=True, default=str)
        if len(text) > RECORD_CAP:
            dropped = len(full.get("decisions") or [])
            full["decisions"] = (full.get("decisions") or [])[:10]
            full["truncated"] = {**(full.get("truncated") or {}),
                                 "decisions": dropped - 10, "bytes": len(text)}
        line = full
    if rotate:
        segment = where / f"seg-{seq:012d}.jsonl"
    data = (json.dumps(line, sort_keys=True, default=str) + "\n").encode("utf-8")
    fd = os.open(segment, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if rotate:
        _prune(meta)
    with _MEM:
        HEALTH.update({"failures": 0, "since": None, "lastError": None,
                       "lastOk": line["at"]})
    return line


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _prune(meta: dict) -> None:
    """Keep the newest segments; name what went as the boundary."""
    segs = _segments()
    if len(segs) <= KEEP_SEGMENTS:
        return
    gone = segs[:len(segs) - KEEP_SEGMENTS]
    upto_seq, upto_at = meta.get("prunedThroughSeq") or 0, None
    for path in gone:
        tail = _tail(path)
        if tail:
            upto_seq = max(upto_seq, int(tail.get("seq") or 0))
            upto_at = tail.get("at") or upto_at
        try:
            path.unlink()
        except OSError:
            pass
    meta = dict(meta)
    meta.update({"prunedThroughSeq": upto_seq,
                 "prunedThroughAt": upto_at or meta.get("prunedThroughAt"),
                 "prunedSegments": int(meta.get("prunedSegments") or 0) + len(gone)})
    records.write_json(_meta_path(), meta, durable=True)


# ── reading ───────────────────────────────────────────────────────────

def _parse(path: Path) -> dict:
    """One segment's lines, parsed once per (size, mtime)."""
    try:
        st = path.stat()
    except OSError:
        return {"lines": [], "malformed": []}
    key = (str(path), st.st_size, st.st_mtime_ns)
    hit = _CACHE.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    lines, malformed = [], []
    try:
        raw = path.read_bytes()
    except OSError:
        return {"lines": [], "malformed": [{"segment": path.name, "line": 0,
                                            "why": "unreadable"}]}
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            malformed.append({"segment": path.name, "line": n,
                              "why": "not a JSON record"})
            continue
        if (not isinstance(rec, dict) or rec.get("v") != VERSION or
                rec.get("kind") not in ("pass", "repeat") or
                not isinstance(rec.get("seq"), int)):
            malformed.append({"segment": path.name, "line": n,
                              "why": "not a version 1 trace record"})
            continue
        rec["_segment"] = path.name
        lines.append(rec)
    out = {"lines": lines, "malformed": malformed}
    _CACHE[str(path)] = (key, out)
    return out


def _groups() -> tuple[list[dict], dict]:
    """Every pass record with its repeats folded in, oldest first, and
    the integrity reading: malformed lines, seq gaps, orphaned repeats."""
    with _LOCK.local:
        segs = _segments()
        parsed = [_parse(p) for p in segs]
    groups: list[dict] = []
    by_id: dict = {}
    malformed, gaps, orphans = [], [], 0
    prev_seq = None
    for seg in parsed:
        malformed.extend(seg["malformed"])
        for rec in seg["lines"]:
            seq = rec["seq"]
            if prev_seq is not None and seq > prev_seq + 1:
                gaps.append({"afterSeq": prev_seq, "beforeSeq": seq,
                             "missing": seq - prev_seq - 1})
            prev_seq = seq if prev_seq is None else max(prev_seq, seq)
            if rec["kind"] == "repeat":
                root = by_id.get(rec.get("of"))
                if root is None:
                    orphans += 1
                    continue
                occ = root["occurrences"]
                occ["count"] += 1
                occ["last"] = rec.get("at")
                occ["lastSeq"] = seq
                occ["ids"].append({"id": rec["id"], "at": rec.get("at")})
                continue
            group = dict(rec)
            group["occurrences"] = {"count": 1, "first": rec.get("at"),
                                    "last": rec.get("at"), "firstSeq": seq,
                                    "lastSeq": seq,
                                    "ids": [{"id": rec["id"], "at": rec.get("at")}]}
            if gaps and gaps[-1]["beforeSeq"] == seq:
                group["seqGapBefore"] = gaps[-1]["missing"]
            groups.append(group)
            by_id[rec["id"]] = group
    return groups, {"malformed": malformed[-50:],
                    "malformedCount": len(malformed),
                    "gaps": gaps[-50:], "orphanedRepeats": orphans}


def _outcome_of(group: dict) -> str:
    if group.get("status") == "error":
        return "error"
    if group.get("status") == "skipped":
        return "skipped"
    for d in group.get("decisions") or []:
        if (d.get("result") or {}).get("outcome"):
            return "action"
    return "no-action"


def _matches(group: dict, card, scope, outcome, action, since, until) -> bool:
    decs = group.get("decisions") or []
    if card:
        wanted = str(card).lstrip("0") or "0"
        if not any(str(d.get("number") or "").lstrip("0") == wanted or
                   d.get("card") == card or
                   str(d.get("card") or "").startswith(f"{card}-") for d in decs):
            return False
    if scope and not any(d.get("scope") == scope for d in decs):
        return False
    if action and not any(d.get("action") == action for d in decs):
        return False
    if outcome:
        kinds = {_outcome_of(group)} | {d.get("outcome") for d in decs}
        if outcome not in kinds:
            return False
    occ = group["occurrences"]
    if since and str(occ.get("last") or "") < since:
        return False
    if until and str(occ.get("first") or "") > until:
        return False
    return True


def _public(group: dict, full: bool = False) -> dict:
    out = {k: v for k, v in group.items() if not k.startswith("_")
           and k != "fingerprint"}
    occ = dict(group["occurrences"])
    ids = occ.pop("ids")
    if full:
        occ["passes"] = ids[-REPEAT_IDS_MAX:]
        occ["passesOmitted"] = max(0, len(ids) - REPEAT_IDS_MAX)
    out["occurrences"] = occ
    out["outcome"] = _outcome_of(group)
    return out


def _time(value) -> str | None:
    """An ISO time or epoch seconds, as the records' ISO text, or None."""
    if value in (None, ""):
        return None
    text = str(value)
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return now_iso(float(text))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?Z?)?", text):
        return text
    raise ValueError(f"{text!r} is not a time (ISO 8601 UTC or epoch seconds)")


def page(card: str | None = None, scope: str | None = None,
         outcome: str | None = None, action: str | None = None,
         since=None, until=None, before: int | None = None,
         limit: int = 25) -> dict:
    """Newest first, `limit` records whose root seq is below `before`.
    A cursor is a seq, so appends during paging never shift a page."""
    limit = max(1, min(int(limit or 25), PAGE_MAX))
    since, until = _time(since), _time(until)
    groups, integrity = _groups()
    picked, more = [], False
    for group in reversed(groups):
        if before is not None and group["seq"] >= int(before):
            continue
        if not _matches(group, card, scope, outcome, action, since, until):
            continue
        if len(picked) == limit:
            more = True
            break
        picked.append(group)
    # time with nothing recorded, between this entry and the older one
    interval = max(1, int(getattr(config, "ORCHESTRATE_INTERVAL", 30) or 30))
    entries = []
    index = {g["id"]: i for i, g in enumerate(groups)}
    for group in picked:
        out = _public(group)
        i = index[group["id"]]
        if i > 0:
            older = groups[i - 1]
            quiet = _gap_seconds(older["occurrences"].get("last"),
                                 group["occurrences"].get("first"))
            if quiet is not None and quiet > 3 * interval:
                out["quietBefore"] = quiet
            if older.get("boot") != group.get("boot"):
                out["restartBefore"] = True
        entries.append(out)
    return {"entries": entries,
            "next": picked[-1]["seq"] if more and picked else None,
            "total": len(groups), "meta": meta(groups, integrity)}


def _gap_seconds(a, b) -> int | None:
    try:
        ta = calendar.timegm(time.strptime(str(a), "%Y-%m-%dT%H:%M:%SZ"))
        tb = calendar.timegm(time.strptime(str(b), "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None
    return int(tb - ta)


def entry(eid: str) -> dict | None:
    """One record by id — its own, or one of its repeats' — with every
    pass it stands for."""
    if not isinstance(eid, str) or not ID_RE.match(eid):
        raise ValueError(f"{eid!r} is not a trace entry id")
    groups, _integrity = _groups()
    for group in groups:
        if group["id"] == eid or any(p["id"] == eid for p in
                                     group["occurrences"]["ids"]):
            return _public(group, full=True)
    return None


def meta(groups: list[dict] | None = None,
         integrity: dict | None = None) -> dict:
    """What the trace covers and how far to trust it: where it starts,
    what retention dropped, recording health, malformed lines and gaps,
    the last pass evaluated and the last one that acted."""
    if groups is None:
        groups, integrity = _groups()
    m = _read_meta()
    last = groups[-1] if groups else None
    acted = next((g for g in reversed(groups) if _outcome_of(g) == "action"), None)
    with _MEM:
        health = dict(HEALTH)
    return {
        "v": VERSION, "kind": KIND, "local": True,
        "where": "this installation's state, never committed or synced",
        "started": m.get("started"),
        "boundary": ({"throughSeq": m.get("prunedThroughSeq"),
                      "throughAt": m.get("prunedThroughAt"),
                      "segments": m.get("prunedSegments")}
                     if m.get("prunedThroughSeq") else None),
        "retention": {"segmentBytes": SEGMENT_BYTES, "keep": KEEP_SEGMENTS,
                      "compaction": "identical consecutive idle passes are "
                                    "one record with every pass's id and time"},
        "integrity": integrity or {},
        "recording": {"failures": health["failures"], "since": health["since"],
                      "lastError": health["lastError"], "lastOk": health["lastOk"]},
        "lastPass": ({"id": last["id"], "at": last["occurrences"]["last"],
                      "status": last.get("status"), "outcome": _outcome_of(last)}
                     if last else None),
        "lastAction": ({"id": acted["id"], "at": acted.get("at")}
                       if acted else None),
        "interval": getattr(config, "ORCHESTRATE_INTERVAL", None),
    }
