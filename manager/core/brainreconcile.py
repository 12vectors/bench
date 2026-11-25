"""Keeping Brain classifications reconciled with the work (task 197).

#196 gave a person an explicit **Evaluate**: selected entries, one bounded
read-only Brain Agent round, proposals validated with their evidence, and
a person's reviewed Apply through the Brain's one writer. This module is
the queue in front of that operation, and nothing else: it decides *which*
entries to evaluate and *when*, launches #196's own `start_evaluate`, and —
only where the policy grants it — applies the proposals bench would select
by default through #196's own batch writer (`evaluate_auto_apply`). It has
no prompt, parser, validator, agent stack or writer of its own.

**Off until a person turns it on.** The policy lives in
`manager/local/brain-reconcile.json`, machine-local like the operating
agreement; absent, nothing here reads past that file, writes, queues or
launches — a fresh install and an upgrade stay *unconfigured*. Turning it
on is `set_policy`, a person's write bound to the revision the page read.
Two authorities, kept apart and both shown:

- **evaluate** — the queue may launch evaluations of entries whose inputs
  moved. A proposal then waits for a person's review in Evaluate.
- **apply** — the proposals bench selects by default (bound evidence, a
  changed classification, nothing blocking) are applied under the policy,
  attributed as such in each entry's history. Needs *evaluate*. Never a
  correction, never an entry with a manual override.

Both are read again from disk immediately before every launch and every
apply, so disabling or revoking reaches the next action; a round already
running finishes and its proposals wait for a person.

**What queues an entry.** An entry's *trigger inputs* are the cards its
`**Source:**` cites (`#<n>`), each with its text revision and stage, and
the application commit a person last refreshed it at. A change to any is
a new input key; the entry is queued once (`card-event`), and further
changes before it launches coalesce into the same place in the queue —
the order events arrive in never matters, because the key is a reading of
the inputs, not a list of events. Entries the queue has never seen are
queued once on enablement (`initial`); **Rebuild** queues every eligible
entry again (`rebuild`). The entry's own revision is not a trigger (an
Apply would otherwise requeue its own work), but every Apply is held to
it. Code changes outside card events never claim freshness: an entry
evaluated at one application commit reads *code changed since* while the
checkout has moved on, and only a person's **Refresh sources** binds the
new commit and queues it (`source-refresh`). Stage alone never
classifies: the agent's rules say a stage is a signal, not proof.

**Bounded.** One evaluation in flight at a time, at most `batch` entries
each, inputs stable for `SETTLE` seconds first, at most `dailyLaunches`
launches in any 24 hours (counted from the intent, so a crash spends
rather than refunds), and `retries` bounded retries of a failed round with
backoff. An unchanged input key is never launched twice, whatever its
outcome; a failure past its retries, a refusal and a cancellation wait for
a person's **Retry**. Nothing is killed for being quiet.

**Durable, and reconciled before anything reruns.** The intent is written
(durably) before `start_evaluate` under a request identity derived from the
batch; a board that died between the two finds the draft by that identity
(`find_request`) or proves nothing launched. A result whose inputs moved
while it ran is `stale` and never applied — the entry is queued again. The
automatic Apply's request identity is derived too, so a lost reply is
answered from the Brain's journal rather than written twice. Only the
designated execution installation holding its lock (`execowner`) runs the
queue: a replica reads and never launches or writes.

The queue's state is machine-local (`state/brain/reconcile.json`); what is
shared is only what the Brain writer commits.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import acceptance
import brain
import braindraft
import brainwrite
import config
import execowner
import records
import state
import taskfiles

VERSION = 1
KIND = "bench-brain-reconcile"
POLICY_KIND = "bench-brain-reconcile-policy"
LIMITS = {"dailyLaunches": (0, 48, 4), "retries": (0, 5, 2),
          "batch": (1, braindraft.MAX_EVAL_ENTRIES, 4)}
POLICY_KEYS = {"v", "kind", "enabled", "evaluate", "apply", "dailyLaunches",
               "retries", "batch", "at", "actor", "history"}
SETTLE = 60.0          # seconds an entry's inputs hold still before it launches
BACKOFF = 120.0        # the first retry's delay; doubled per attempt
DAY = 24 * 3600
MAX_HISTORY = 20
MAX_ACTIONS = 50
MAX_OPS = 60

# What an entry's reconciliation state says. `sync-pending` and `drift`
# are read, never stored: they are facts about the writer's journal and
# the checkout now.
STATES = ("queued", "running", "waiting", "review", "applied", "current", "stale",
          "failed", "blocked", "cancelled", "held")
OPEN_OPS = ("intent", "launched")
REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")

LOCK = threading.RLock()


class Refused(ValueError):
    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def policy_path() -> Path:
    return config.LOCAL / "brain-reconcile.json"


def state_path() -> Path:
    return config.STATE / "brain" / "reconcile.json"


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _now() -> str:
    return taskfiles.now_iso()


# ── the policy ─────────────────────────────────────────────────────────


def read_policy() -> dict:
    """The policy in force, read from disk now. Absent is `unconfigured`
    and grants nothing; one that does not parse or breaks the contract is
    `invalid` and grants nothing either. A read."""
    path = policy_path()
    base = {"state": "unconfigured", "enabled": False, "evaluate": False, "apply": False,
            "revision": "none", "problem": None, "at": None, "actor": None,
            "history": [], "file": "manager/local/brain-reconcile.json",
            **{k: v[2] for k, v in LIMITS.items()}}
    if not path.exists() and not path.is_symlink():
        return base
    if path.is_symlink() or not path.is_file():
        return dict(base, state="invalid", revision="unreadable",
                    problem="the policy file is not a regular file")
    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return dict(base, state="invalid", revision="unreadable",
                    problem="the policy file is not readable JSON")
    revision = hashlib.sha256(raw_bytes).hexdigest()[:16]
    problem = _policy_problem(raw)
    if problem:
        return dict(base, state="invalid", revision=revision, problem=problem)
    return dict(base, state="configured", revision=revision,
                enabled=raw["enabled"], evaluate=raw["evaluate"], apply=raw["apply"],
                at=raw.get("at"), actor=raw.get("actor"),
                history=list(raw.get("history") or [])[-MAX_HISTORY:],
                **{k: raw.get(k, v[2]) for k, v in LIMITS.items()})


def _policy_problem(raw) -> str | None:
    if not isinstance(raw, dict):
        return "the policy is not a JSON object"
    if raw.get("v") != VERSION or raw.get("kind") != POLICY_KIND:
        return f"the policy is not version {VERSION} of {POLICY_KIND}"
    unknown = set(raw) - POLICY_KEYS
    if unknown:
        return "unknown policy keys: " + ", ".join(sorted(unknown))
    for k in ("enabled", "evaluate", "apply"):
        if not isinstance(raw.get(k), bool):
            return f"{k} is true or false"
    if raw["apply"] and not raw["evaluate"]:
        return "apply needs evaluate: nothing is applied that was not evaluated"
    for k, (low, high, _) in LIMITS.items():
        v = raw.get(k, LIMITS[k][2])
        if not isinstance(v, int) or isinstance(v, bool) or not low <= v <= high:
            return f"{k} is a whole number from {low} to {high}"
    return None


def effective(policy: dict | None = None, owner: dict | None = None) -> dict:
    """What the policy and the execution owner grant now, each word with
    why — the one reading every action asks immediately before it acts."""
    p = policy or read_policy()
    o = owner or execowner.authorizes()
    on = p["state"] == "configured" and p["enabled"]
    evaluates = on and p["evaluate"]
    applies = evaluates and p["apply"]
    if p["state"] == "unconfigured":
        why = ("not configured: nothing is queued or launched until a person turns "
               "reconciliation on")
    elif p["state"] == "invalid":
        why = f"the policy is invalid ({p['problem']}), so it grants nothing"
    elif not p["enabled"]:
        why = "turned off: nothing new is queued or launched"
    elif not evaluates:
        why = "on, without the evaluate authority: nothing is launched"
    elif not o["allowed"]:
        why = f"this board does not execute: {o['reason']}"
    elif applies:
        why = ("evaluates entries whose inputs moved, and applies the proposals bench "
               "selects by default under this policy")
    else:
        why = ("evaluates entries whose inputs moved; every proposal waits for a "
               "person's review and Apply")
    return {"on": on, "evaluate": evaluates, "apply": applies,
            "executes": bool(o["allowed"]), "owner": o.get("code"),
            "ownerReason": o.get("reason"), "runs": evaluates and bool(o["allowed"]),
            "why": why}


def set_policy(payload: dict) -> dict:
    """A person's explicit change of the policy — the only way it is turned
    on, off or given an authority. Bound to the revision the page read
    (`expected`, `none` before the first), refused otherwise with nothing
    written. It launches nothing: the next beat reads it."""
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    with LOCK:
        current = read_policy()
        if payload.get("expected") != current["revision"]:
            raise Refused("the reconciliation policy changed since this page read it — "
                          "read it again; nothing was written", 409, stale=True,
                          current=current["revision"])
        if current["state"] == "invalid" and current["revision"] == "unreadable":
            raise Refused("the policy file cannot be read; fix or remove it by hand", 409)
        doc = {"v": VERSION, "kind": POLICY_KIND}
        for k in ("enabled", "evaluate", "apply"):
            v = payload.get(k, current[k])
            if not isinstance(v, bool):
                raise Refused(f"{k} is true or false", field=k)
            doc[k] = v
        for k in LIMITS:
            doc[k] = payload.get(k, current[k])
        problem = _policy_problem(doc)
        if problem:
            raise Refused(problem)
        who = acceptance.actor()
        changed = [k for k in ("enabled", "evaluate", "apply", *LIMITS)
                   if doc[k] != current[k] or current["state"] != "configured"]
        if not changed:
            raise Refused("that is the policy already in force; nothing was written")
        line = {"at": _now(), "actor": who["name"] or "unknown",
                "actorSource": who["source"],
                "change": {k: doc[k] for k in changed}}
        doc.update(at=line["at"], actor=line["actor"],
                   history=(current["history"] + [line])[-MAX_HISTORY:])
        records.write_json(policy_path(), doc, durable=True)
        after = read_policy()
    state.broadcast({"type": "brain"})
    return {"policy": _public_policy(after), "effective": effective(after)}


def _public_policy(p: dict) -> dict:
    return {k: p[k] for k in ("state", "enabled", "evaluate", "apply", "revision",
                              "problem", "at", "actor", "history", "file", *LIMITS)}


# ── the queue's state ──────────────────────────────────────────────────


def _blank() -> dict:
    return {"v": VERSION, "kind": KIND, "entries": {}, "ops": {}, "launches": [],
            "seq": 0, "refresh": {}, "actions": {}}


def _load() -> dict:
    st = records.read_json(state_path(), expect=dict)
    if not st or st.get("v") != VERSION or st.get("kind") != KIND:
        return _blank()
    for k, v in _blank().items():
        st.setdefault(k, v)
    return st


def _save(st: dict) -> None:
    ops = st["ops"]
    if len(ops) > MAX_OPS:
        done = sorted((k for k, o in ops.items() if o["status"] not in OPEN_OPS
                       and o["status"] != "proposed"),
                      key=lambda k: ops[k].get("at") or 0)
        for k in done[:len(ops) - MAX_OPS]:
            ops.pop(k)
    st["launches"] = [t for t in st["launches"] if t > time.time() - DAY]
    records.write_json(state_path(), st, durable=True)


# ── inputs ─────────────────────────────────────────────────────────────


def _cards() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for stage in taskfiles.collect()["stages"]:
        for t in stage.get("tasks") or []:
            if t.get("number"):
                found.setdefault(str(int(t["number"])), {
                    "rev": t.get("rev"), "stage": stage["slug"], "file": t.get("file")})
    return found


def _cited(entry: dict) -> list[str]:
    out: list[str] = []
    for src in entry.get("sources") or []:
        # the index says each source honestly: a card, or a review on one
        number = src.get("card") if isinstance(src, dict) and \
            src.get("kind") in ("card", "review") else None
        if number and str(number).isdigit():
            n = str(int(number))
            if n not in out:
                out.append(n)
    return out


def _inputs(entry: dict, cards: dict, refresh: dict) -> dict:
    """An entry's trigger inputs, read now: each cited card's revision and
    stage (or that it is not on the board), and the application commit a
    person last refreshed it at."""
    rows = []
    for n in _cited(entry):
        c = cards.get(n)
        rows.append({"card": n, "rev": c["rev"] if c else None,
                     "stage": c["stage"] if c else None})
    return {"cards": rows, "refresh": refresh.get(entry["id"])}


def _key(inputs: dict) -> str:
    return "k" + _digest(inputs)[:20]


def _eligible() -> tuple[list[dict], list[dict]]:
    """The live maintained entries the queue may evaluate, and the ones a
    manual override holds."""
    live, held = [], []
    for e in brain.index().get("entries") or []:
        if e.get("tombstone"):
            continue
        (held if e.get("override") else live).append(e)
    return live, held


def _app_commit() -> str | None:
    return braindraft.app_identity().get("commit")


# ── one tick ───────────────────────────────────────────────────────────


def tick(now: float | None = None) -> dict:
    """One bounded pass: settle what launched, notice what moved, launch at
    most one evaluation. Runs only on the designated execution
    installation; with reconciliation unconfigured it reads one file."""
    now = time.time() if now is None else now
    with LOCK:
        policy = read_policy()
        if policy["state"] == "unconfigured":
            return {"ran": False, "code": "unconfigured"}
        # the designated installation takes its own lock, exactly as the
        # scheduler's pass does — never another installation's
        read = execowner.reading()
        if read["role"] == "designated-here" and not read["lockHeld"]:
            execowner.acquire_lock()
        owner = execowner.authorizes()
        if not owner["allowed"]:
            return {"ran": False, "code": owner["code"]}
        st = _load()
        _recover(st, now)
        _observe(st, now)
        eff = effective(read_policy(), owner)
        launched = None
        if eff["evaluate"]:
            _detect(st, now)
            launched = _launch(st, now)
        _save(st)
    if launched:
        state.broadcast({"type": "brain"})
    return {"ran": True, "launched": launched}


def _rec(st: dict, eid: str) -> dict:
    return st["entries"].setdefault(eid, {"state": None, "attempts": 0})


def _queue(st: dict, eid: str, key: str, inputs: dict, reason: str, triggers: list,
           now: float, revision: str | None) -> None:
    rec = _rec(st, eid)
    if rec.get("state") == "queued":
        # coalesce: one place in the queue, the newest inputs, every trigger
        rec["triggers"] = sorted(set(rec.get("triggers") or []) | set(triggers))[:20]
        if rec.get("key") != key:
            rec["changedAt"] = now
    else:
        st["seq"] += 1
        rec.update(seq=st["seq"], queuedAt=now, changedAt=now, reason=reason,
                   triggers=sorted(set(triggers))[:20], retryAt=None)
        if reason not in ("retry", "stale-result"):
            rec["attempts"] = 0
    rec.update(state="queued", key=key, inputs=inputs, revision=revision,
               why=None, code=None)


def _changed_cards(before: dict | None, after: dict) -> list[str]:
    old = {c["card"]: c for c in (before or {}).get("cards") or []}
    out = []
    for c in after["cards"]:
        if old.get(c["card"]) != c:
            out.append(f"#{c['card']}")
    for n in old:
        if n not in {c["card"] for c in after["cards"]}:
            out.append(f"#{n}")
    return out


def _detect(st: dict, now: float) -> None:
    live, held = _eligible()
    cards = _cards()
    ids = set()
    for e in held:
        rec = _rec(st, e["id"])
        if rec.get("state") not in ("running",):
            rec.update(state="held", code="override",
                       why="a person's manual override holds this classification; it "
                           "is never re-evaluated automatically until it is cleared")
    for e in live:
        ids.add(e["id"])
        inputs = _inputs(e, cards, st["refresh"])
        key = _key(inputs)
        rec = st["entries"].get(e["id"])
        if rec is None or rec.get("state") is None:
            _queue(st, e["id"], key, inputs, "initial", [], now, e["revision"])
            continue
        if rec.get("state") == "held":
            # the override was cleared: evaluate it on its current inputs
            _queue(st, e["id"], key, inputs, "override-cleared", [], now, e["revision"])
            continue
        if rec.get("state") == "queued":
            if rec.get("key") != key:
                _queue(st, e["id"], key, inputs, rec.get("reason") or "card-event",
                       _changed_cards(rec.get("inputs"), inputs), now, e["revision"])
            continue
        if rec.get("state") == "running":
            continue
        last = (rec.get("evaluated") or {}).get("key") or rec.get("key")
        if last != key:
            reason = ("source-refresh" if (rec.get("inputs") or {}).get("refresh")
                      != inputs["refresh"] else "card-event")
            _queue(st, e["id"], key, inputs, reason,
                   _changed_cards(rec.get("inputs"), inputs), now, e["revision"])
    for eid in list(st["entries"]):
        rec = st["entries"][eid]
        if eid not in ids and rec.get("state") == "queued":
            rec.update(state="cancelled", code="gone",
                       why="the entry is retired, gone or now held by an override")


def _launch(st: dict, now: float) -> str | None:
    if any(o["status"] in OPEN_OPS and _op_running(o) for o in st["ops"].values()):
        return None
    policy = read_policy()
    queued = sorted((eid for eid, r in st["entries"].items() if r.get("state") == "queued"),
                    key=lambda eid: st["entries"][eid].get("seq") or 0)
    ready = [eid for eid in queued
             if (st["entries"][eid].get("retryAt") or 0) <= now
             and now - (st["entries"][eid].get("changedAt") or 0) >= SETTLE]
    if not ready:
        return None
    used = [t for t in st["launches"] if t > now - DAY]
    if len(used) >= policy["dailyLaunches"]:
        for eid in queued:
            st["entries"][eid].update(code="budget-exhausted", why=(
                f"waiting: {len(used)} of {policy['dailyLaunches']} launches in the last "
                f"24 hours are spent; it launches when one frees"))
        return None
    batch = ready[:policy["batch"]]
    keys = {eid: st["entries"][eid]["key"] for eid in batch}
    attempt = max(st["entries"][eid].get("attempts") or 0 for eid in batch)
    # a person's Retry is a new decision, so it is a new identity; an
    # unchanged key with no new attempt and no Retry is the same batch
    asks = {eid: st["entries"][eid].get("asks") or 0 for eid in batch}
    op_key = "brc-" + _digest({"keys": keys, "attempt": attempt, "asks": asks})[:24]
    if op_key in st["ops"]:
        # this exact batch already ran: an unchanged input key is never
        # launched twice — the entries wait for a change or a person
        for eid in batch:
            prev = st["ops"][op_key]
            st["entries"][eid].update(state="failed", code="already-evaluated", why=(
                f"these inputs were already evaluated ({prev['status']}); it waits for "
                f"a change or a person's Retry"))
        return None
    triggers = sorted({t for eid in batch for t in st["entries"][eid].get("triggers") or []})
    reasons = sorted({st["entries"][eid].get("reason") or "card-event" for eid in batch})
    op = {"status": "intent", "at": now, "entries": batch, "keys": keys,
          "attempt": attempt, "reasons": reasons, "triggers": triggers,
          "policy": policy["revision"], "app": _app_commit()}
    st["ops"][op_key] = op
    st["launches"].append(now)
    for eid in batch:
        st["entries"][eid].update(state="running", op=op_key, code=None, why=None)
    _save(st)     # the intent is durable before anything is asked of the agent
    try:
        pre = braindraft.evaluate_preflight({"entries": batch})
    except braindraft.Refused as exc:
        _refused(st, op_key, str(exc))
        return None
    if not pre["ready"]:
        _refused(st, op_key, (pre["blockers"] or ["the evaluation is not ready"])[0],
                 pre["blockers"])
        return None
    text = ("Reconcile the classification of " + ", ".join(batch) + " — "
            + ", ".join(reasons) + (f" ({', '.join(triggers)} changed)" if triggers else ""))
    try:
        out = braindraft.start_evaluate({"request": op_key, "entries": batch,
                                         "approval": pre["approval"],
                                         "text": text[:braindraft.MAX_REQUEST]},
                                        origin="reconcile")
    except braindraft.Refused as exc:
        _refused(st, op_key, str(exc))
        return None
    op.update(status="launched", draft=out["draft"]["id"], launchedAt=now)
    for eid in batch:
        st["entries"][eid].update(draft=out["draft"]["id"])
    return op_key


def _op_running(op: dict) -> bool:
    if op["status"] == "intent":
        return True
    try:
        d = braindraft.get(op.get("draft"))
    except braindraft.Refused:
        return False
    return d["status"] == "running"


def _refused(st: dict, op_key: str, why: str, blockers: list | None = None) -> None:
    op = st["ops"][op_key]
    op.update(status="refused", why=why[:400])
    named = [eid for eid in op["entries"]
             if any(eid in b for b in (blockers or [why]))]
    for eid in op["entries"]:
        rec = st["entries"][eid]
        if named and eid not in named:
            # not this entry's problem: it goes back in the queue, in its place
            rec.update(state="queued", code=None, why=None)
        else:
            rec.update(state="blocked", code="refused", why=why[:400],
                       evaluated={"key": rec.get("key"), "at": _now(),
                                  "outcome": "refused", "app": op.get("app")})


def _recover(st: dict, now: float) -> None:
    """An intent with no receipt: a board stopped between recording it and
    hearing from `start_evaluate`. The draft is found by the request
    identity, or nothing launched — reconciled before anything reruns."""
    for key, op in st["ops"].items():
        if op["status"] != "intent":
            continue
        found = braindraft.find_request(key)
        if found is not None:
            op.update(status="launched", draft=found["id"], recovered=_now())
            for eid in op["entries"]:
                st["entries"].get(eid, {}).update(draft=found["id"])
        else:
            op.update(status="not-launched", recovered=_now(),
                      why="the board stopped before the evaluation launched; nothing ran")
            for eid in op["entries"]:
                rec = st["entries"].get(eid)
                if rec and rec.get("state") == "running" and rec.get("op") == key:
                    rec.update(state="queued", code="not-launched",
                               why="the launch was interrupted; it is queued again",
                               attempts=(rec.get("attempts") or 0) + 1)


def _observe(st: dict, now: float) -> None:
    for key, op in list(st["ops"].items()):
        if op["status"] not in ("launched", "proposed"):
            continue
        try:
            d = braindraft.get(op["draft"])
        except braindraft.Refused:
            op.update(status="lost")
            _settle_entries(st, key, op, "failed", "lost",
                            "the evaluation is no longer on this board")
            continue
        status = d["status"]
        if status == "running":
            continue
        if status in ("questions", "ready"):
            for eid in op["entries"]:
                rec = st["entries"].get(eid) or {}
                if rec.get("op") == key and rec.get("state") == "running":
                    rec.update(state="waiting", code="questions", why=(
                        "the Brain Agent asked a question only a person can answer; "
                        "answer it in Evaluate classifications"))
            continue
        if status == "proposed" and op.get("round") != d.get("proposalRound"):
            _settle_proposal(st, key, op, d)
            continue
        if status == "applied":
            applied = set((d.get("applied") or {}).get("entries") or [])
            for eid in op["entries"]:
                rec = st["entries"].get(eid) or {}
                if rec.get("op") == key and rec.get("state") in ("review", "running"):
                    if eid in applied:
                        rec.update(state="applied", code="applied-by-person"
                                   if d.get("applyBy") != "policy" else "applied",
                                   why=None, applied={
                                       "request": d["applied"].get("request"),
                                       "at": d["applied"].get("at"),
                                       "classification": (d["applied"].get(
                                           "classifications") or {}).get(eid)})
                    else:
                        rec.update(state="current", code="not-applied",
                                   why="the evaluation was applied without this entry")
            op["status"] = "done"
            continue
        if status == "failed":
            _settle_failure(st, key, op, d, now)
            continue
        if status in ("cancelled", "discarded", "expired"):
            op["status"] = status
            _settle_entries(st, key, op, "cancelled", status,
                            f"the evaluation was {status}; it waits for a change or a "
                            f"person's Retry")


def _settle_entries(st, key, op, new_state, code, why) -> None:
    for eid in op["entries"]:
        rec = st["entries"].get(eid) or {}
        if rec.get("op") == key and rec.get("state") in ("running", "waiting", "review"):
            rec.update(state=new_state, code=code, why=why,
                       evaluated={"key": op["keys"].get(eid), "at": _now(),
                                  "outcome": code, "app": op.get("app")})


def _settle_failure(st, key, op, d, now) -> None:
    policy = read_policy()
    err = d.get("error") or {}
    op.update(status="failed", why=(err.get("why") or "")[:400])
    for eid in op["entries"]:
        rec = st["entries"].get(eid) or {}
        if rec.get("op") != key or rec.get("state") not in ("running", "waiting"):
            continue
        attempts = (rec.get("attempts") or 0) + 1
        rec["attempts"] = attempts
        if attempts <= policy["retries"] and effective(policy)["evaluate"]:
            rec.update(state="queued", reason="retry", code="retry",
                       retryAt=now + BACKOFF * 2 ** (attempts - 1),
                       why=f"the round failed ({err.get('code') or 'failed'}); retry "
                           f"{attempts} of {policy['retries']} after a backoff")
        else:
            rec.update(state="failed", code=err.get("code") or "failed",
                       why=(f"the round failed ({err.get('why') or 'no reason given'})"
                            f"; {attempts - 1} of {policy['retries']} retries used — "
                            f"it waits for a change or a person's Retry")[:500],
                       evaluated={"key": op["keys"].get(eid), "at": _now(),
                                  "outcome": "failed", "app": op.get("app")})


def _settle_proposal(st: dict, key: str, op: dict, d: dict) -> None:
    """A round proposed. Entries whose inputs moved while it ran are stale
    and queued again; the rest are applied under the policy where it grants
    that now, or wait for a person's review."""
    op["round"] = d.get("proposalRound")
    op["status"] = "proposed"
    ev = (d.get("evaluate") or {}).get("evaluation") or {}
    items = {i["entry"]: i for i in ev.get("items") or []}
    refused = {r.get("entry"): r for r in ev.get("refused") or [] if isinstance(r, dict)}
    live, _ = _eligible()
    by_id = {e["id"]: e for e in live}
    cards = _cards()
    fresh, now_keys = [], {}
    for eid in op["entries"]:
        e = by_id.get(eid)
        if e is None:
            continue
        inputs = _inputs(e, cards, st["refresh"])
        now_keys[eid] = (_key(inputs), inputs, e)
        if now_keys[eid][0] == op["keys"].get(eid):
            fresh.append(eid)
    applied: dict[str, dict] = {}
    blocked_by: dict[str, dict] = {}
    closed = False
    auto_why = None
    eff = effective(read_policy())
    candidates = [eid for eid in fresh if eid in items
                  and items[eid].get("proposed") != items[eid].get("current")]
    if eff["apply"] and candidates:
        request = f"{key}-apply-{op['round']}"
        try:
            out = braindraft.evaluate_auto_apply(d["id"], request, candidates)
            for row in out.get("items") or []:
                if row.get("selected") and not row.get("blocked") and \
                        row["entry"] in candidates:
                    applied[row["entry"]] = row
                elif row.get("blocked"):
                    blocked_by[row["entry"]] = row["blocked"]
            op.update(applyRequest=request, status="done")
            closed = True
        except braindraft.Refused as exc:
            for row in exc.extra.get("items") or []:
                if row.get("blocked"):
                    blocked_by[row["entry"]] = row["blocked"]
            auto_why = ("not applied automatically: " + (
                "" if blocked_by else f"{exc} ") + f"({exc.extra.get('code')})")
    elif candidates and not eff["apply"]:
        auto_why = ("the apply authority is not granted"
                    if eff["evaluate"] else "reconciliation was turned off or revoked "
                    "while this ran, so nothing is applied automatically")
    for eid in op["entries"]:
        rec = st["entries"].get(eid)
        if not rec or rec.get("op") != key or \
                rec.get("state") not in ("running", "waiting", "review"):
            continue
        evaluated = {"key": op["keys"].get(eid), "at": _now(), "app": op.get("app"),
                     "draft": d["id"], "round": op["round"]}
        item = items.get(eid)
        if eid not in now_keys:
            rec.update(state="cancelled", code="gone", why="the entry is gone or held "
                       "by an override now", evaluated=dict(evaluated, outcome="gone"))
        elif eid not in fresh:
            k, inputs, e = now_keys[eid]
            rec["lastStale"] = {"at": _now(), "draft": d["id"]}
            _queue(st, eid, k, inputs, "stale-result",
                   _changed_cards(rec.get("inputs"), inputs), time.time(), e["revision"])
            rec.update(code="stale-result", why=(
                "its inputs moved while the evaluation ran, so that result was not used; "
                "it is queued again"))
        elif eid in applied:
            row = applied[eid]
            rec.update(state="applied", code="applied", why=None,
                       evaluated=dict(evaluated, outcome="applied"),
                       explanation=row.get("explanation"),
                       applied={"request": op.get("applyRequest"), "at": _now(),
                                "classification": row.get("classification")})
        elif item is None:
            why = (refused.get(eid) or {}).get("why") or (
                "the Brain Agent did not evaluate it")
            rec.update(state="failed", code="not-evaluated", why=str(why)[:400],
                       evaluated=dict(evaluated, outcome="not-evaluated"))
        elif item.get("proposed") == item.get("current"):
            rec.update(state="current", code="unchanged", why=(
                f"evaluated: it stays {item.get('current')} — {item.get('reason')}")[:400],
                evaluated=dict(evaluated, outcome="unchanged"),
                explanation=item.get("explanation"))
        else:
            why = auto_why or "it is not one bench selects by default"
            if eid in blocked_by:
                why = f"not applied automatically: {blocked_by[eid].get('why')}"
            if closed:
                # the automatic Apply closed this evaluation; what it left
                # out is said, and a Retry or a person's Evaluate asks again
                rec.update(state="blocked", code=(blocked_by.get(eid) or {}).get(
                    "code") or "not-default", why=(
                    f"proposed {item.get('proposed')}; {why} — the evaluation was "
                    f"applied without it, so Retry, or Evaluate it by hand")[:500],
                    evaluated=dict(evaluated, outcome="proposed"),
                    proposed=item.get("proposed"), explanation=item.get("explanation"))
                continue
            rec.update(state="review", code="review", why=(
                f"proposed {item.get('proposed')}; waits for a person's review — "
                + why)[:500],
                evaluated=dict(evaluated, outcome="proposed"),
                proposed=item.get("proposed"), explanation=item.get("explanation"))
    if op["status"] != "done" and not any(
            (st["entries"].get(eid) or {}).get("state") == "review"
            and (st["entries"].get(eid) or {}).get("op") == key for eid in op["entries"]):
        op["status"] = "done"


# ── a person's actions ─────────────────────────────────────────────────

ACTIONS = ("rebuild", "refresh", "retry", "cancel")


def act(payload: dict) -> dict:
    """A person's action on the queue: **Rebuild** queues every eligible
    entry again; **Refresh sources** binds the application's current commit
    to the named entries (or all), which queues them; **Retry** puts failed,
    blocked or cancelled entries back; **Cancel** takes queued entries out
    and cancels a running evaluation. Each is one request identity: the
    same again answers with its first result. None launches anything."""
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    action = payload.get("action")
    request = payload.get("request")
    if action not in ACTIONS:
        raise Refused("the action is one of " + ", ".join(ACTIONS), field="action")
    if not isinstance(request, str) or not REQUEST_RE.match(request):
        raise Refused("name the request identity", field="request")
    ids = payload.get("entries")
    if ids is not None and (not isinstance(ids, list) or not all(
            isinstance(i, str) and brain.ID_RE.match(i) for i in ids) or len(ids) > 200):
        raise Refused("entries are Brain entry ids", field="entries")
    cancel_draft = None
    with LOCK:
        eff = effective()
        if not eff["on"]:
            raise Refused(f"reconciliation is not on: {eff['why']}", 409, code="off")
        if not eff["executes"]:
            raise Refused(f"only the execution installation changes the queue: "
                          f"{eff['ownerReason']}", 409, code="not-owner")
        st = _load()
        seen = st["actions"].get(request)
        if seen is not None:
            if seen.get("action") != action or seen.get("entries") != ids:
                raise Refused("this request identity was used for another action", 409)
            return dict(seen.get("result") or {}, duplicate=True)
        now = time.time()
        live, _ = _eligible()
        by_id = {e["id"]: e for e in live}
        chosen = ids if ids is not None else list(by_id)
        unknown = [i for i in chosen if i not in by_id and i not in st["entries"]]
        if unknown:
            raise Refused(f"{unknown[0]} is not an entry the queue may evaluate", 404)
        cards = _cards()
        touched = []
        if action == "refresh":
            commit = _app_commit()
            if not commit:
                raise Refused("the application's commit cannot be read, so there is "
                              "nothing to refresh to", 409)
            for eid in chosen:
                if eid in by_id:
                    st["refresh"][eid] = commit
        for eid in chosen:
            rec = st["entries"].get(eid) or {}
            e = by_id.get(eid)
            if action in ("rebuild", "refresh"):
                if e is None:
                    continue
                inputs = _inputs(e, cards, st["refresh"])
                key = _key(inputs)
                if rec.get("state") == "running":
                    # a refresh makes the round in flight about old inputs:
                    # its result reads stale and the entry is queued again
                    if action == "refresh":
                        touched.append(eid)
                    continue
                _queue(st, eid, key, inputs, "source-refresh" if action == "refresh"
                       else "rebuild", [], now, e["revision"])
                # rebuild and refresh are a person's ask: no settle delay
                st["entries"][eid]["changedAt"] = now - SETTLE
                touched.append(eid)
            elif action == "retry":
                if e is None or rec.get("state") not in ("failed", "blocked", "cancelled"):
                    continue
                inputs = _inputs(e, cards, st["refresh"])
                rec["attempts"] = 0
                rec["state"] = None
                _queue(st, eid, _key(inputs), inputs, "retry-by-person", [], now,
                       e["revision"])
                st["entries"][eid].update(changedAt=now - SETTLE, attempts=0)
                touched.append(eid)
            elif action == "cancel":
                if rec.get("state") == "queued":
                    rec.update(state="cancelled", code="cancelled-by-person",
                               why="cancelled by a person; it waits for a change or a "
                                   "Retry",
                               evaluated={"key": rec.get("key"), "at": _now(),
                                          "outcome": "cancelled"})
                    touched.append(eid)
                elif rec.get("state") in ("running", "waiting") and rec.get("draft"):
                    cancel_draft = rec["draft"]
                    touched.append(eid)
        if action != "cancel":
            # a person's Rebuild, Refresh or Retry is a new decision, so it
            # is a new launch identity even where the inputs are unchanged
            for eid in touched:
                st["entries"][eid]["asks"] = (st["entries"][eid].get("asks") or 0) + 1
        result = {"action": action, "entries": touched}
        st["actions"][request] = {"action": action, "entries": ids, "result": result,
                                  "at": now}
        if len(st["actions"]) > MAX_ACTIONS:
            for k in sorted(st["actions"], key=lambda k: st["actions"][k]["at"])[
                    :len(st["actions"]) - MAX_ACTIONS]:
                st["actions"].pop(k)
        _save(st)
    if cancel_draft:
        try:
            braindraft.cancel({"id": cancel_draft})
        except braindraft.Refused:
            pass
    state.broadcast({"type": "brain"})
    return result


# ── reading ────────────────────────────────────────────────────────────


STATE_WORDS = {
    "queued": "Queued", "running": "Evaluating", "waiting": "Waiting for your answer",
    "review": "Waiting for your review", "applied": "Applied", "current": "Up to date",
    "stale": "Stale", "failed": "Failed", "blocked": "Blocked", "cancelled": "Cancelled",
    "held": "Held by a manual override", "sync-pending": "Applied; sync pending",
    "drift": "Code changed since evaluated", "unqueued": "Not yet reconciled",
}


def reading() -> dict:
    """The queue as the page reads it: the policy and what it grants, the
    execution owner, the budget, and per entry its state in words, why,
    its bound inputs (cards and their revisions, the application commit it
    was evaluated at against the checkout now) and its receipts. A read:
    nothing is queued, launched or written."""
    policy = read_policy()
    eff = effective(policy)
    st = _load() if state_path().exists() else _blank()
    now = time.time()
    rows = []
    counts: dict[str, int] = {}
    if policy["state"] != "unconfigured" or st["entries"]:
        live, held = _eligible()
        cards = _cards()
        commit = _app_commit()
        everything = {e["id"]: e for e in live + held}
        for eid in sorted(everything):
            e = everything[eid]
            rec = st["entries"].get(eid) or {}
            inputs = _inputs(e, cards, st["refresh"])
            word = rec.get("state") or ("held" if e.get("override") else "unqueued")
            evaluated = rec.get("evaluated") or {}
            shown = word
            moved = bool(evaluated) and evaluated.get("key") != _key(inputs) \
                and word not in ("queued", "running")
            if moved:
                shown = "stale"
            sync = None
            if word == "applied" and (rec.get("applied") or {}).get("request"):
                try:
                    op = brainwrite.operation(rec["applied"]["request"])
                except brainwrite.Refused:
                    op = None
                if op:
                    push = op.get("push") or {}
                    commit_state = (op.get("commit") or {}).get("state")
                    sync = {"commit": commit_state, "push": push.get("state"),
                            "request": rec["applied"]["request"]}
                    if push.get("state") not in ("shared", "off") and \
                            commit_state != "off":
                        shown = "sync-pending"
            code_drift = bool(evaluated.get("app")) and commit is not None and \
                evaluated.get("app") != commit and \
                (st["refresh"].get(eid) or evaluated.get("app")) != commit
            counts[shown] = counts.get(shown, 0) + 1
            expl = rec.get("explanation")
            rows.append({
                "entry": eid, "title": e.get("title"),
                "classification": e.get("classification"), "revision": e.get("revision"),
                "state": shown, "stored": word, "label": STATE_WORDS.get(shown, shown),
                "why": ("its inputs changed since it was evaluated; it is queued on the "
                        "next pass" if moved and eff["runs"] else
                        "its inputs changed since it was evaluated" if moved
                        else rec.get("why")),
                "code": rec.get("code"), "reason": rec.get("reason"),
                "triggers": rec.get("triggers") or [],
                "cards": inputs["cards"], "refreshedAt": st["refresh"].get(eid),
                "evaluatedAt": evaluated.get("at"), "evaluatedApp": evaluated.get("app"),
                "outcome": evaluated.get("outcome"),
                "codeDrift": code_drift, "appNow": commit,
                "attempts": rec.get("attempts") or 0,
                "retryAt": rec.get("retryAt") if word == "queued" else None,
                "draft": rec.get("draft"), "proposed": rec.get("proposed"),
                "explanation": ({"state": "pending"} if (expl or {}).get("state")
                                == "pending" else expl),
                "applied": rec.get("applied"), "sync": sync,
                "override": bool(e.get("override")),
                "position": None})
        order = sorted((r for r in rows if r["stored"] == "queued"),
                       key=lambda r: (st["entries"][r["entry"]].get("seq") or 0))
        for n, r in enumerate(order, 1):
            r["position"] = n
    used = [t for t in st["launches"] if t > now - DAY]
    ops = sorted(st["ops"].items(), key=lambda kv: kv[1].get("at") or 0, reverse=True)
    return {
        "v": VERSION, "kind": KIND, "policy": _public_policy(policy), "effective": eff,
        "budget": {"dailyLaunches": policy["dailyLaunches"], "used": len(used),
                   "left": max(0, policy["dailyLaunches"] - len(used)),
                   "retries": policy["retries"], "batch": policy["batch"],
                   "settleSeconds": int(SETTLE), "inFlight": sum(
                       1 for o in st["ops"].values() if o["status"] in OPEN_OPS)},
        "counts": counts, "entries": rows,
        "operations": [{"key": k, "status": o["status"], "at": o.get("at"),
                        "entries": o["entries"], "draft": o.get("draft"),
                        "reasons": o.get("reasons"), "triggers": o.get("triggers"),
                        "app": o.get("app"), "why": o.get("why"),
                        "applyRequest": o.get("applyRequest")} for k, o in ops[:12]],
        "authorities": {
            "evaluate": "the queue may launch read-only evaluations of entries whose "
                        "inputs moved; proposals wait for a person's review",
            "apply": "the proposals bench selects by default are applied under this "
                     "policy, attributed as such; never a correction, never an entry "
                     "with a manual override"},
        "rules": [
            "a change to a card an entry cites queues it; changes before it launches "
            "coalesce into one place in the queue",
            "code that changed outside a card event is shown as code changed since "
            "evaluated, and is bound only by a person's Refresh sources",
            "the same inputs are never evaluated twice; a stale result is never applied",
            "one evaluation at a time, a bounded batch, a daily launch budget and "
            "bounded retries; nothing is killed for being quiet",
            "only the designated execution installation launches or applies",
        ],
    }


def beat(interval: float | None = None) -> None:
    """The reconciliation thread: one tick every interval, forever. With
    reconciliation unconfigured a tick reads one file and returns. Its first
    tick waits one interval, so a restart launches nothing as it comes up."""
    while True:
        time.sleep(interval or config.ORCHESTRATE_INTERVAL)
        try:
            tick()
        except Exception as exc:  # noqa: BLE001 — the beat must survive
            state.record_board_event({
                "kind": "error", "actor": "board", "file": None,
                "summary": f"Brain reconciliation failed: {type(exc).__name__}: "
                           f"{str(exc)[:200]}"})
