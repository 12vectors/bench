"""What the machine was doing while a check ran (task 179): host load and
concurrency, observed beside the runner and never part of a verdict.

A slow or failed run is easy to blame on "load" and hard to show. The
evidence record already says when a check was asked for, how long it
waited for a slot (`queued`, kept only over a second), when its
processes started and ended. It does not say what else was running, or
how busy the host was. This module watches that, for every verification
this board runs, and keeps it in a sidecar beside the evidence:

    state/evidence/load/<evidence id>.json

**Diagnostic, never identity.** The sidecar is not the evidence record,
no reader of a verdict opens it, and this module is imported by none of
`resultidentity.RUNNER_FILES` — so adding it changes no runner revision,
no result identity and no reuse decision, and an unchanged check's pass
keeps answering for it. A record written before this existed simply has
no sidecar, which reads as *not observed*, never as *quiet*.

**Observed, not measured to the millisecond.** A beat (`beat`, every
`INTERVAL` seconds) reads the runner's own registry of runs in flight
(`verify.RUNNING`) under the board's lock. So a check's first and last
readings are within one interval of its real start and end, and a check
that lived less than an interval may not be seen at all. Each reading
holds:

- the phase each live run was in: `waiting` (reserved, not yet holding a
  slot — making its checkout or queued behind the parallel limit),
  `preparing` or `running`;
- how many other checks were in each phase, the configured slot limit,
  and how many agent runs this board was tracking;
- the host's 1/5/15-minute load average, its CPU count and the memory
  the kernel reported available, where the host offers them.

Over a run's life it keeps the first and last reading, the reading when
it was first seen gone (`end`), the mean and maximum 1-minute load over
the readings, the most checks seen executing at once, and up to
`MAX_PEERS` other checks seen beside it, each with the seconds observed
together and executing together. More peers are counted, not listed.

**Co-occurrence is not cause.** Two checks running at once, a high load
average and a slow run are three observations. Nothing here says one
caused another; `verify/contention-report` compares like with like and
says the same.

**Bounded and sanitized.** Numbers, evidence ids and check names (the
operator's own words, already on the page), each clipped. No command, no
argument, no path, no environment value, no log line.

A board that stops leaves its open sidecars without an `end` and with
`complete: false`: the ending was not observed, and that is what they
say. Nothing here launches, cancels, throttles or schedules a check.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import config
import evidence
import records
import state
import verify


VERSION = 1
KIND = "bench-check-load"
INTERVAL = 5.0          # seconds between readings
WRITE_EVERY = 60.0      # a live run's sidecar is rewritten at most this often
MAX_PEERS = 16          # other checks listed by name; more are counted
NAME_CAP = 64           # a check name, clipped
# A gap longer than this between two readings (a stalled beat, a suspended
# machine) is not counted as time observed together: the beat did not see
# what happened in it.
MAX_STEP = 3 * INTERVAL

WHY = ("diagnostic only: what this board observed beside the check while "
       "it ran. Not part of the result identity, the verdict or reuse; "
       "overlap and load are observations, never a cause")

PHASES = ("waiting", "preparing", "running")

_OBS: dict[str, dict] = {}      # evidence id -> what is being gathered
_GUARD = threading.Lock()       # one sample at a time


def directory() -> Path:
    return config.EVIDENCE_DIR / "load"


def path_of(evidence_id: str) -> Path:
    return directory() / f"{evidence.slug(evidence_id)}.json"


# ── what the host says ─────────────────────────────────────────────────

def host() -> dict:
    """The host's load average, CPU count and available memory, each None
    where the platform does not say. Read from the kernel; runs nothing."""
    try:
        one, five, fifteen = os.getloadavg()
        load = [round(one, 2), round(five, 2), round(fifteen, 2)]
    except (OSError, AttributeError):
        load = None
    return {"load": load, "cpus": os.cpu_count(), "memAvailableMiB": _mem_available()}


def _mem_available() -> int | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


# ── what the board is running ──────────────────────────────────────────

def phase_of(record: dict) -> str | None:
    """`waiting`, `preparing`, `running` — or None for a run that is not
    live. A record reads `running` from the moment it is reserved; its log
    is opened only once it holds a slot, so a live run with no log yet is
    still waiting (provisioning its checkout, or queued)."""
    outcome = record.get("outcome")
    if outcome not in evidence.LIVE:
        return None
    if outcome == "preparing":
        return "preparing"
    try:
        has_log = evidence.log_path_of(record["id"]).exists()
    except (KeyError, OSError, ValueError):
        has_log = False
    return "running" if has_log else "waiting"


def snapshot() -> dict[str, dict]:
    """Every live verification, as `{id: {check, phase}}`, read under the
    board's lock from the runner's own registry."""
    with state.LOCK:
        live = [(eid, dict(r)) for eid, r in verify.RUNNING.items()]
        agents = sum(1 for r in state.AGENTS.values()
                     if r.get("status") == "running")
    out: dict[str, dict] = {}
    for eid, record in live:
        record.setdefault("id", eid)
        phase = phase_of(record)
        if phase and evidence.is_evidence_id(eid):
            out[eid] = {"check": _name(record.get("check")), "phase": phase,
                        "doctor": bool(record.get("doctor"))}
    return {"runs": out, "agents": agents}


def _name(value) -> str | None:
    if not isinstance(value, str):
        return None
    clean = "".join(ch for ch in value if ch.isprintable())
    return clean[:NAME_CAP]


def _reading(at: float, me: str, runs: dict, agents: int, hostinfo: dict) -> dict:
    others = {p: 0 for p in PHASES}
    for eid, run in runs.items():
        if eid != me:
            others[run["phase"]] += 1
    return {"at": round(at, 3), "phase": runs[me]["phase"] if me in runs else None,
            "others": others, "slots": config.VERIFY_PARALLEL,
            "agents": agents, "load": hostinfo.get("load"),
            "memAvailableMiB": hostinfo.get("memAvailableMiB")}


# ── the beat ───────────────────────────────────────────────────────────

def sample(now: float | None = None, seen: dict | None = None,
           hostinfo: dict | None = None) -> list[str]:
    """One reading of every live verification. Opens a sidecar for a run
    seen for the first time, folds this reading into every run still
    live, and closes the sidecar of a run no longer live. Returns the ids
    whose sidecar was written. `seen` and `hostinfo` are for tests."""
    with _GUARD:
        now = time.time() if now is None else now
        seen = snapshot() if seen is None else seen
        hostinfo = host() if hostinfo is None else hostinfo
        runs, agents = seen.get("runs") or {}, int(seen.get("agents") or 0)
        written: list[str] = []
        for eid, run in runs.items():
            obs = _OBS.get(eid)
            reading = _reading(now, eid, runs, agents, hostinfo)
            if obs is None:
                obs = _OBS[eid] = _open(eid, run, reading, hostinfo)
                _fold(obs, reading, runs, eid, step=0.0)
                _write(obs, now)
                written.append(eid)
                continue
            step = now - obs["_prev"]
            _fold(obs, reading, runs, eid,
                  step=step if 0 < step <= MAX_STEP else 0.0)
            if now - obs["_written"] >= WRITE_EVERY:
                _write(obs, now)
                written.append(eid)
        for eid in [e for e in _OBS if e not in runs]:
            obs = _OBS.pop(eid)
            obs["doc"]["end"] = {"at": round(now, 3),
                                 "load": hostinfo.get("load"),
                                 "memAvailableMiB": hostinfo.get("memAvailableMiB")}
            obs["doc"]["complete"] = True
            _write(obs, now)
            written.append(eid)
        return written


def _open(eid: str, run: dict, reading: dict, hostinfo: dict) -> dict:
    doc = {
        "v": VERSION, "kind": KIND, "evidence": eid, "check": run["check"],
        "doctor": run.get("doctor") or None,
        "observer": {"interval": INTERVAL, "cpus": hostinfo.get("cpus"),
                     "boot": state.BOOT},
        "first": reading, "last": reading, "end": None,
        "phases": {p: None for p in PHASES},   # first time seen in each
        "load1": {"samples": 0, "mean": None, "max": None},
        "most": {"executing": 0, "live": 0, "agents": 0},
        "peers": [], "peersOmitted": 0,
        "complete": False, "why": WHY,
    }
    return {"doc": doc, "_prev": reading["at"], "_written": 0.0,
            "_sum": 0.0, "_peers": {}, "_omitted": set()}


def _fold(obs: dict, reading: dict, runs: dict, me: str, *, step: float) -> None:
    doc = obs["doc"]
    doc["last"] = reading
    phase = reading["phase"]
    if phase and doc["phases"].get(phase) is None:
        doc["phases"][phase] = reading["at"]
    load = reading.get("load")
    if load:
        stats = doc["load1"]
        stats["samples"] += 1
        obs["_sum"] += load[0]
        stats["mean"] = round(obs["_sum"] / stats["samples"], 2)
        stats["max"] = load[0] if stats["max"] is None else max(stats["max"], load[0])
    executing = sum(1 for r in runs.values() if r["phase"] != "waiting")
    most = doc["most"]
    most["executing"] = max(most["executing"], executing)
    most["live"] = max(most["live"], len(runs))
    most["agents"] = max(most["agents"], reading.get("agents") or 0)
    mine_exec = phase in ("preparing", "running")
    for eid, run in runs.items():
        if eid == me:
            continue
        peer = obs["_peers"].get(eid)
        if peer is None:
            if len(obs["_peers"]) >= MAX_PEERS:
                obs["_omitted"].add(eid)
                continue
            # first seen now: the time before this reading was not observed
            obs["_peers"][eid] = {"evidence": eid, "check": run["check"],
                                  "together": 0.0, "executing": 0.0}
            continue
        peer["together"] += step
        if mine_exec and run["phase"] != "waiting":
            peer["executing"] += step
    obs["_prev"] = reading["at"]


def _write(obs: dict, now: float) -> None:
    doc = obs["doc"]
    doc["peers"] = [{**p, "together": round(p["together"], 1),
                     "executing": round(p["executing"], 1)}
                    for p in obs["_peers"].values()]
    doc["peersOmitted"] = len(obs["_omitted"])
    obs["_written"] = now
    try:
        directory().mkdir(parents=True, exist_ok=True)
        records.write_json(path_of(doc["evidence"]), doc)
    except (OSError, ValueError):
        # An observation that cannot be kept changes nothing about the
        # check: it is a diagnostic, and its absence reads as unobserved.
        pass


def read(evidence_id: str) -> dict | None:
    """The sidecar for one verification, or None — never observed, or
    written before task 179."""
    if not evidence.is_evidence_id(evidence_id):
        return None
    doc = records.read_json(path_of(evidence_id), None, expect=dict)
    if not doc or doc.get("kind") != KIND or doc.get("v") != VERSION:
        return None
    return doc


def reset() -> None:
    """Forget every run being gathered (tests)."""
    with _GUARD:
        _OBS.clear()


def beat() -> None:
    """The board's thread: one `sample` every `INTERVAL` seconds. A reading
    that fails is skipped; the next one tries again."""
    while True:
        time.sleep(INTERVAL)
        try:
            sample()
        except Exception:  # noqa: BLE001 — a diagnostic must never stop
            pass
