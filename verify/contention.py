"""Read bench's evidence records and say, per verification, how long it
waited, prepared and executed, and what else was running (task 179).

Stdlib only, and it imports no bench module: it reads the JSON files
`manager/local/state/evidence/` already holds, and the diagnostic
sidecars `checkload` writes beside them under `load/`. Nothing here
writes, runs or schedules anything.

Three intervals, kept apart because they answer different questions:

- **before start** — from the request to the first process (preparation,
  else the check). It includes making the checkout and waiting for a slot.
- **slot wait** — the part of that bench spent queued behind the parallel
  limit, as the record says (`queued`). The runner keeps it only when it
  exceeded a second, so its absence is "under a second, or a record from
  before the field" and is reported as unknown, never as zero.
- **execution** — preparation plus the check process, from the first
  process start to the end.

**Overlap is computed from the records alone**, so old records take part:
two runs overlap when their execution windows intersect. That is
co-occurrence. A slow run beside another one is an observation; whether
one slowed the other needs a comparison of like with like (the same check,
full scope, comparable outcome) quiet against overlapped — which `compare`
gives, with its sample size — and even that is not proof.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

LOAD_KIND = "bench-check-load"
# Outcomes that reached the end of their own run. A denied request ran
# nothing; a cancelled or restarted one stopped early and says nothing
# about how long the check takes.
COMPARABLE = ("passed", "failed", "prepare-failed")


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def read_dir(evidence_dir) -> tuple[list[dict], dict[str, dict]]:
    """Every evidence record and every load sidecar under `evidence_dir`.
    Files that do not parse are skipped; a reuse record is not evidence."""
    root = Path(evidence_dir)
    records: list[dict] = []
    for path in sorted(root.glob("*.json")):
        doc = _load(path)
        if isinstance(doc, dict) and isinstance(doc.get("id"), str) \
                and doc.get("id", "").startswith("ev:"):
            records.append(doc)
    sidecars: dict[str, dict] = {}
    for path in sorted((root / "load").glob("*.json")):
        doc = _load(path)
        if isinstance(doc, dict) and doc.get("kind") == LOAD_KIND \
                and isinstance(doc.get("evidence"), str):
            sidecars[doc["evidence"]] = doc
    return records, sidecars


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def phases(record: dict) -> dict:
    """The record's intervals. Each is a number of seconds or None, and
    every None is named in `unknown` with why."""
    unknown: list[str] = []
    requested = _num(record.get("requested"))
    started = _num(record.get("started"))
    ended = _num(record.get("ended"))
    prep = [p for p in record.get("prepare") or [] if isinstance(p, dict)]
    prep_starts = [_num(p.get("started")) for p in prep if _num(p.get("started"))]
    first = min(prep_starts) if prep_starts else started
    prepare = sum(_num(p.get("duration")) or 0.0 for p in prep) if prep else 0.0
    before = (first - requested) if (first is not None and requested is not None) else None
    if before is None:
        unknown.append("before-start: no process start or no request time on record")
    execution = (ended - first) if (first is not None and ended is not None) else None
    if execution is None:
        unknown.append("execution: the run did not start, or has not ended")
    check = _num(record.get("duration")) if started is not None else None
    queued = _num(record.get("queued"))
    if queued is None:
        unknown.append("slot-wait: not on record (under a second, or written "
                       "before the field) — unknown, not zero")
    return {
        "id": record.get("id"),
        "scope": record.get("scope"), "outcome": record.get("outcome"),
        "commit": ((record.get("source") or {}).get("commit") or "")[:12] or None,
        "requested": requested, "start": first, "end": ended,
        "beforeStart": _round(before), "slotWait": _round(queued),
        # no steps on record: 0 for a run that started (it had none to
        # prepare), unknown for one that never ran (a denial, say)
        "prepare": (_round(prepare) if prep
                    else 0.0 if started is not None else None),
        "checkProcess": _round(check) if check is not None else None,
        "execution": _round(execution), "unknown": unknown,
        "checkName": record.get("check"),
        "doctor": bool(record.get("doctor")),
    }


def _round(value):
    return round(value, 1) if isinstance(value, (int, float)) else None


def overlaps(rows: list[dict]) -> dict[str, list[dict]]:
    """For each run, the other runs whose execution windows intersect its
    own, with the seconds of intersection. Runs with no window take no
    part."""
    windows = [(r["id"], r["checkName"], r["start"], r["end"]) for r in rows
               if r.get("start") is not None and r.get("end") is not None
               and r["end"] > r["start"]]
    windows.sort(key=lambda w: w[2])
    out: dict[str, list[dict]] = {w[0]: [] for w in windows}
    for i, (a, ca, sa, ea) in enumerate(windows):
        for b, cb, sb, eb in windows[i + 1:]:
            if sb >= ea:
                break
            seconds = min(ea, eb) - max(sa, sb)
            if seconds <= 0:
                continue
            out[a].append({"evidence": b, "check": cb, "seconds": round(seconds, 1)})
            out[b].append({"evidence": a, "check": ca, "seconds": round(seconds, 1)})
    return out


def analyse(records: list[dict], sidecars: dict[str, dict] | None = None, *,
            check: str | None = None, include_scoped: bool = False) -> dict:
    """Rows for every run asked about, each with its intervals, the runs
    it overlapped, and its observed load where a sidecar exists."""
    sidecars = sidecars or {}
    # A diagnosis's runs are not listed, but they still ran on this host:
    # they take part in overlap, so a run beside one never reads as quiet.
    rows = [phases(r) for r in records]
    lapped = overlaps(rows)
    picked = []
    for row in rows:
        if row["doctor"]:
            continue
        if check and row["checkName"] != check:
            continue
        if row["scope"] and not include_scoped:
            continue
        row = dict(row)
        others = lapped.get(row["id"], [])
        row["overlapped"] = sorted(others, key=lambda o: -o["seconds"])
        row["overlapSeconds"] = round(sum(o["seconds"] for o in others), 1)
        side = sidecars.get(row["id"])
        row["observed"] = _observed(side)
        picked.append(row)
    picked.sort(key=lambda r: r.get("requested") or 0)
    return {"runs": picked, "comparison": compare(picked)}


def _observed(side: dict | None) -> dict | None:
    if not side:
        return None
    first, last = side.get("first") or {}, side.get("last") or {}
    return {"load1Mean": (side.get("load1") or {}).get("mean"),
            "load1Max": (side.get("load1") or {}).get("max"),
            "samples": (side.get("load1") or {}).get("samples"),
            "cpus": (side.get("observer") or {}).get("cpus"),
            "mostExecuting": (side.get("most") or {}).get("executing"),
            "mostAgents": (side.get("most") or {}).get("agents"),
            "slots": first.get("slots") if first else last.get("slots"),
            "complete": bool(side.get("complete"))}


def compare(rows: list[dict]) -> list[dict]:
    """Per check, comparable full runs split by whether anything else was
    executing beside them: counts and the median and range of execution.
    A side with fewer than two runs gets no median; with fewer than three
    on either side the comparison says it is too small to read."""
    out = []
    names = sorted({r["checkName"] for r in rows if r.get("checkName")})
    for name in names:
        mine = [r for r in rows if r["checkName"] == name and not r.get("scope")
                and r.get("outcome") in COMPARABLE and r.get("execution") is not None]
        quiet = [r["execution"] for r in mine if not r.get("overlapped")]
        busy = [r["execution"] for r in mine if r.get("overlapped")]
        entry = {"check": name, "quiet": _spread(quiet), "overlapped": _spread(busy)}
        if len(quiet) < 3 or len(busy) < 3:
            entry["note"] = ("too small to read: fewer than three comparable runs "
                             "on a side. Co-occurrence is not cause either way")
        elif entry["quiet"]["median"] and entry["overlapped"]["median"]:
            entry["ratio"] = round(entry["overlapped"]["median"] / entry["quiet"]["median"], 2)
            entry["note"] = ("median execution, overlapped over quiet. An "
                             "association in this sample, not a cause")
        out.append(entry)
    return out


def _spread(values: list[float]) -> dict:
    values = sorted(values)
    return {"n": len(values),
            "median": round(statistics.median(values), 1) if len(values) >= 2 else None,
            "min": values[0] if values else None,
            "max": values[-1] if values else None}


def render(report: dict, limit: int = 50) -> str:
    """The report as text a person reads in a terminal."""
    lines = ["Verification timing and overlap (seconds). Overlap is "
             "co-occurrence, not cause.", ""]
    runs = report["runs"][-limit:]
    if len(report["runs"]) > limit:
        lines.append(f"(showing the newest {limit} of {len(report['runs'])} runs)")
    head = f"{'evidence':<28} {'check':<20} {'outcome':<14} {'before':>7} {'slot':>6} {'prep':>7} {'exec':>8} {'overlap':>8}  with"
    lines.append(head)
    for r in runs:
        with_ = ", ".join(f"{o['check']}" for o in r["overlapped"][:3])
        if len(r["overlapped"]) > 3:
            with_ += f" +{len(r['overlapped']) - 3}"
        lines.append(f"{str(r['id'])[:28]:<28} {str(r['checkName'])[:20]:<20} "
                     f"{str(r['outcome'])[:14]:<14} {_cell(r['beforeStart']):>7} "
                     f"{_cell(r['slotWait']):>6} {_cell(r['prepare']):>7} "
                     f"{_cell(r['execution']):>8} {_cell(r['overlapSeconds']):>8}  {with_}")
        if r.get("observed"):
            o = r["observed"]
            lines.append(f"{'':<28} observed: load1 mean {o['load1Mean']} max {o['load1Max']} "
                         f"on {o['cpus']} cpus; at most {o['mostExecuting']} checks "
                         f"executing, {o['mostAgents']} agents; slots {o['slots']}"
                         + ("" if o["complete"] else "; end not observed"))
    lines += ["", "Comparable full runs, quiet versus overlapped (execution):"]
    for c in report["comparison"]:
        q, b = c["quiet"], c["overlapped"]
        lines.append(f"  {c['check']}: quiet n={q['n']} median {_cell(q['median'])} "
                     f"[{_cell(q['min'])}–{_cell(q['max'])}]; overlapped n={b['n']} "
                     f"median {_cell(b['median'])} [{_cell(b['min'])}–{_cell(b['max'])}]"
                     + (f"; ratio {c['ratio']}" if c.get("ratio") else ""))
        if c.get("note"):
            lines.append(f"    {c['note']}")
    lines += ["", "slot: blank means unknown (under a second, or an older record), never zero."]
    return "\n".join(lines)


def _cell(value) -> str:
    return "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)
