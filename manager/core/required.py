"""Run required checks: one person's request for the missing required
checks of the commit a card would merge (design #94 §10.2–§10.3).

A card in `checks-needed` used to cost one armed ↻ per check in the ◫
sheet. This is the same request, made once: every required check whose
requirement reads `needed` at the candidate commit H is asked for, in the
order the merge rule lists them, through `verify.request` — the one door
every verification comes through — under a person's **work** reading,
exactly as ↻ asks today. It owns no runner, no policy and no evidence.

What it adds is the bookkeeping a batch needs to stay honest:

- **The plan is a reading.** `plan()` reads the gate, the candidate and
  the work reading's checks and says what a click would do — request,
  join, skip, or not request and why. It writes nothing, starts nothing
  and creates no checkout, which is why the confirmation may be opened
  freely.
- **Only `needed` is requested.** A met requirement is skipped; one
  already running is joined (never doubled); one whose newest answer at H
  failed, could not run or was refused is *not* rerun behind a person's
  back — that is a result to read, and ↻ in the ◫ sheet is the explicit
  way to run it again. A check the work reading cannot ask for is shown
  with the configuration remedy; nothing here widens what a person's
  request may run.
- **The candidate is re-read between requests.** Before each one the
  branch tip, the merge rule's revision and the checkout's cleanliness are
  read again; any of them moving stops the batch with the reason, and the
  checks it had not reached are listed as not requested. A result whose
  source is another commit is never reported as this candidate's answer.
- **Nothing retries by itself.** A cancelled batch, a held run's
  cancelled checks and a board that restarted mid-batch all leave an
  explicit record — `cancelled`, `error` with *the board restarted* — and
  the next **Run required checks** is a person's click. Nothing schedules
  a request because a requirement exists.

Records are machine-local, one per card, under `state/evidence/required/`
beside the evidence they point at, and say paths nowhere.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import config
import gates
import profiles
import state
import taskfiles
import verify

VERSION = 1

# The reading a person's request rides: the ◫ sheet's ↻ asks with no run
# and no intent, which `verify.request` resolves as `work`. The same here,
# so the batch can never be granted a check ↻ could not ask for.
INTENT = "work"

# Each item's state, in the words a row shows. The outcomes a request can
# end in are verify's own words (`verify.OUTCOMES`); these are the batch's.
ITEM_STATES = {
    "request": "will be requested",
    "queued": "waiting its turn in this batch",
    "requesting": "requested — bench is running it",
    "met": "already met on this commit — skipped",
    "joined": "already running on this commit — joined, not doubled",
    "reused": ("answered by an equal, current result already on record — "
               "nothing was run"),
    "problem": ("not requested: its newest result on this commit did not "
                "pass. Read it first; ↻ in the ◫ evidence sheet runs it "
                "again"),
    "unavailable": ("not requested: a person's work reading may not ask for "
                    "it"),
    "not-requested": "not requested: the batch stopped before reaching it",
    "other-candidate": ("ran against another commit, so it is not this "
                        "candidate's answer"),
}

# Why a batch could not start, or stopped. One slug each, so the page and
# the tests branch on data rather than on prose.
REFUSALS = {
    "ungated": ("no merge rule guards this card, so no check is required "
                "here and there is nothing to run"),
    "rules-invalid": ("this board's merge rules do not resolve, so nothing "
                      "is required that could be asked for — fix them in "
                      "Settings → Merge rules"),
    "no-candidate": ("there is no implementation commit on this board to "
                     "check — nothing was requested"),
    "dirty": ("the checkout holding this card's branch has uncommitted "
              "changes, so a check would not be about the commit that "
              "would merge. Commit, then run checks"),
    "profile-blocked": ("a person's work reading does not resolve on this "
                        "board, so no check can be asked for"),
    "nothing-needed": ("no required check is waiting to be asked for on "
                       "this commit"),
    "head-moved": ("the candidate commit has moved since this was read — "
                   "read it again before confirming"),
    "authority": ("Run required checks is a person's action. A run asks "
                  "for its own checks through bench-verify, under the "
                  "profile it was launched with"),
    "unknown-card": "there is no such card on this board",
    "no-batch": ("no batch of required checks is running on this card — "
                 "nothing was stopped"),
}

STOPS = {
    "source-drift": ("the card's branch moved to another commit, so the "
                     "rest was not requested — run required checks again "
                     "for the new commit"),
    "policy-changed": ("the merge rules or the check definitions changed, "
                       "so the rest was not requested — run required "
                       "checks again under the rules as they are now"),
    "dirty": ("uncommitted changes appeared in the checkout, so the rest "
              "was not requested — commit, then run checks"),
    "cancelled": ("a person stopped the batch, so the rest was not "
                  "requested. Nothing retries by itself"),
    "board-restart": ("this board stopped during the batch, so what the "
                      "check in flight would have found is unknown and the "
                      "rest was not requested. Nothing retries by itself"),
    "runner-error": ("bench could not make a request, so the rest was not "
                     "requested"),
}

# What a click does, said once, so the confirmation, the docs and the
# tests read the same sentence. Built from the ◫ sheet's ↻ words.
SIDE_EFFECTS = (
    "For each check listed as requested, bench runs that check's command "
    "in this card's own checkout (or, for a source check, in a detached "
    "checkout it makes and removes), with its preparation steps first, "
    "one check at a time and in this order. Each writes an evidence record "
    "and a log. A check already running is joined, a requirement already "
    "met is skipped, and an equal, current result already on record is "
    "returned rather than run again. It moves no card, touches no branch, "
    "publishes nothing and changes no setting. Build output, caches and "
    "temporary files land in the directory bench points HOME and TMPDIR "
    "at. Nothing is retried by itself.")

PREPARATION = (
    "Preparation steps (installing dependencies, a disposable fixture) run "
    "first, as part of each check's own request. A preparation that fails "
    "is recorded as preparation failed — the check itself never ran, and "
    "that is not a test failure.")


class Refused(ValueError):
    """A batch that could not start, with the slug that says why."""

    def __init__(self, code: str, why: str | None = None, status: int = 409):
        super().__init__(why or REFUSALS.get(code, code))
        self.code = code
        self.status = status


_LOCK = threading.Lock()
# The batch in flight per card, in this board's memory. On disk a record
# marked running with no entry here is a batch an earlier board owned.
ACTIVE: dict[str, dict] = {}


def dir_for() -> Path:
    return config.EVIDENCE_DIR / "required"


def _path(filename: str) -> Path:
    stem = filename[:-3] if filename.endswith(".md") else filename
    return dir_for() / f"{stem}.json"


def _store(batch: dict) -> dict:
    path = _path(batch["task"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".batch-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(batch, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return batch


def latest(filename: str) -> dict | None:
    """The newest batch for this card: the one in flight, else the record
    the last one left."""
    with _LOCK:
        live = ACTIVE.get(filename)
        if live is not None:
            return json.loads(json.dumps(live))
    try:
        return json.loads(_path(filename).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def running() -> dict[str, dict]:
    """Every card with a batch in flight on this board, summarised."""
    with _LOCK:
        files = list(ACTIVE)
    return {f: summary(latest(f)) for f in files}


# ── reading what a click would do ──────────────────────────────────────


def _find(filename: str, stage: str | None) -> dict:
    if not filename.endswith(".md") or "/" in filename:
        raise Refused("unknown-card", status=404)
    for group in taskfiles.collect()["stages"]:
        for task in group["tasks"]:
            if task["file"] == filename and (not stage
                                             or task["stage"] == stage):
                return task
    raise Refused("unknown-card", status=404)


def scope_of(task: dict) -> str:
    """The merge this card's requirements guard: a phase member's merge
    into its phase branch, a phase's acceptance, or an ordinary card's."""
    if task.get("phase") and not task.get("isPhase"):
        return "member"
    return gates.scope_for(task)


def _checkout_of(ref: str) -> Path | None:
    """The worktree that has `ref` checked out, if any. A local read."""
    found = config._app_git("worktree", "list", "--porcelain")
    if found.returncode != 0:
        return None
    path = None
    for line in found.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{ref}" and path:
            return Path(path)
    return None


def dirty(ref: str) -> list[str]:
    """Uncommitted paths in the checkout holding `ref`; empty when it is
    clean or no checkout holds it (a branch with no checkout cannot be
    dirty). Read with bench's read-only git."""
    where = _checkout_of(ref)
    if where is None or not where.is_dir():
        return []
    try:
        found = config.git_run(where, "status", "--porcelain",
                               "--untracked-files=normal")
    except (OSError, ValueError):
        return ["(the checkout could not be read)"]
    if found.returncode != 0:
        return ["(the checkout could not be read)"]
    return [line[3:] for line in found.stdout.splitlines() if line.strip()]


def remedy_unavailable(check: str) -> str:
    """The configuration remedy for a required check a person's work
    reading cannot ask for. It names the setting; it never grants it."""
    return (f"the merge rule requires '{check}', and a person's work "
            f"reading may not ask for it. Add '{check}' to the work "
            f"profile's \"verify\" list in the execution policy "
            f"(Settings → Advanced policy), or change the merge rule "
            f"(Settings → Merge rules). Bench does not widen what a "
            f"request may run")


def _definition(found: dict, manifest: dict) -> dict:
    """What one check would run, for the confirmation: its command, where,
    and its preparation steps apart from it."""
    steps = list(manifest.get("prepare") or []) + list(found.get("prepare")
                                                       or [])
    return {"command": found.get("run"), "root": found.get("root"),
            "timeout": found.get("timeout"),
            "prepare": [{"name": s.get("name"), "command": s.get("run"),
                         "network": bool(s.get("network")),
                         "required": s.get("required", True) is not False}
                        for s in steps]}


def _classify(row: dict, offered: dict) -> dict:
    """One requirement row, as a batch item."""
    reason = row.get("reason") or ""
    if row["state"] == "pending":
        reason = row.get("reason") if row.get("reason") in verify.LIVE \
            else "running"
    cls = gates.severity(reason if row["state"] != "met" else "passed")
    item = {"check": row.get("check"), "scope": row.get("scope"),
            "name": row.get("name"), "coverage": row.get("coverage"),
            "reason": reason, "class": cls,
            "plain": gates.plain(reason, row.get("commit")),
            "evidence": row.get("evidence"), "why": row.get("why")}
    if row["state"] == "met":
        item["state"] = "met"
    elif row["state"] == "pending":
        item["state"] = "joined"
    elif row["state"] == "overridden" or cls != "needed":
        item["state"] = "problem"
    elif row.get("check") not in offered:
        item["state"] = "unavailable"
        item["why"] = remedy_unavailable(row.get("check") or "")
        item["remedy"] = item["why"]
    else:
        item["state"] = "request"
    return item


def plan(filename: str, stage: str | None = None) -> dict:
    """What **Run required checks** would do for this card, now.

    A reading: the gate, the candidate, the checkout's cleanliness and the
    work reading's checks, each from the authority that answers for it.
    It writes nothing, fetches nothing, starts no process but read-only
    git, and pins nothing (`profiles.manifest` without `pin_inputs`)."""
    task = _find(filename, stage)
    scope = scope_of(task)
    policy = gates.gate_policy()
    head = gates.head_for(filename, scope)
    out = {"v": VERSION, "task": filename, "stage": task["stage"],
           "scope": scope, "head": head.get("commit"), "ref": head["ref"],
           "policyRevision": policy.get("revision"), "items": [],
           "refusal": None, "why": None, "dirty": [],
           "sideEffects": SIDE_EFFECTS, "preparation": PREPARATION,
           "intent": INTENT, "batch": public(latest(filename))}
    decision = gates.evaluate(filename, scope, task=task, policy=policy)

    def refuse(code: str, why: str | None = None) -> dict:
        out["refusal"] = out["refusal"] or code
        out["why"] = out["why"] or why or REFUSALS[code]
        return out

    if decision["decision"] == "blocked" and any(
            r.get("reason") == "policy-refused"
            for r in decision.get("requirements") or []):
        return refuse("rules-invalid")
    if decision["decision"] == "ungated":
        return refuse("ungated")
    manifest = profiles.manifest(INTENT, filename, task["stage"])
    offered = {c["name"]: c for c in verify.available(manifest)}
    for row in decision.get("requirements") or []:
        if row.get("kind") != "check":
            continue
        item = _classify(row, offered)
        if item["check"] in offered:
            item["definition"] = _definition(offered[item["check"]], manifest)
        out["items"].append(item)
    out["requestable"] = len([i for i in out["items"]
                              if i["state"] == "request"])
    if not head.get("commit"):
        return refuse("no-candidate")
    if manifest.get("blockers"):
        return refuse("profile-blocked",
                      f"{REFUSALS['profile-blocked']}: "
                      f"{manifest['blockers'][0]}")
    out["dirty"] = dirty(head["ref"])
    if out["dirty"]:
        return refuse("dirty", f"{REFUSALS['dirty']} "
                               f"({len(out['dirty'])} uncommitted "
                               f"path{'' if len(out['dirty']) == 1 else 's'})")
    if not out["requestable"]:
        return refuse("nothing-needed")
    return out


# ── the batch ──────────────────────────────────────────────────────────


def start(filename: str, stage: str | None = None, *,
          head: str | None = None, actor: str = "you",
          agent: str | None = None, wait: bool = False) -> dict:
    """Begin a batch, on a person's click. Returns the batch record at once
    (the requests run on a thread of their own); `wait` runs them inline,
    for a caller that must see the ending (the tests).

    Refused, with nothing requested, when a run asks (`agent`), when the
    plan refuses, or when `head` — the commit the confirmation was shown —
    is not the candidate now. A batch already in flight for this card is
    returned rather than doubled."""
    if agent:
        raise Refused("authority", status=403)
    with _LOCK:
        live = ACTIVE.get(filename)
        if live is not None:
            found = json.loads(json.dumps(live))
            found["joinedBatch"] = True
            return public(found)
    reading = plan(filename, stage)
    if head and reading["head"] and head != reading["head"]:
        raise Refused("head-moved",
                      f"{REFUSALS['head-moved']} (it was "
                      f"{str(head)[:12]}, it is now "
                      f"{str(reading['head'])[:12]})")
    if reading["refusal"]:
        raise Refused(reading["refusal"], reading["why"])
    now = time.time()
    batch = {
        "v": VERSION,
        "id": f"rq-{int(now * 1000):x}-{os.getpid():x}",
        "task": filename, "stage": reading["stage"],
        "scope": reading["scope"], "head": reading["head"],
        "ref": reading["ref"], "policyRevision": reading["policyRevision"],
        "intent": INTENT, "actor": actor, "requested": now,
        "state": "running", "ended": None, "stop": None, "stopWhy": None,
        "cancel": False, "location": "local",
        "items": [{k: v for k, v in item.items() if k != "definition"}
                  for item in reading["items"]],
    }
    for item in batch["items"]:
        if item["state"] == "request":
            item.update(state="queued", asked=True)
    with _LOCK:
        if filename in ACTIVE:              # lost a race with another click
            found = json.loads(json.dumps(ACTIVE[filename]))
            found["joinedBatch"] = True
            return public(found)
        ACTIVE[filename] = batch
    _store(batch)
    state.record_board_event({
        "kind": "verify", "actor": actor, "file": filename,
        "summary": (f"run required checks on {filename} at "
                    f"{str(batch['head'])[:12]} — "
                    f"{_names(batch, ('queued',))}")})
    state.broadcast({"type": "verify"})
    if wait:
        _run(batch)
    else:
        threading.Thread(target=_run, args=(batch,), daemon=True).start()
    return public(latest(filename))


def _names(batch: dict, states: tuple[str, ...]) -> str:
    names = [_label(i) for i in batch["items"] if i["state"] in states]
    return ", ".join(names) or "nothing"


def _label(item: dict) -> str:
    return item["check"] + (f" · {item['scope']}" if item.get("scope") else "")


def _save(batch: dict) -> None:
    with _LOCK:
        snapshot = json.loads(json.dumps(batch))
    _store(snapshot)
    state.broadcast({"type": "verify"})


def _halt(batch: dict, code: str, why: str | None = None) -> None:
    with _LOCK:
        batch["stop"] = code
        batch["stopWhy"] = why or STOPS[code]
        for item in batch["items"]:
            if item["state"] == "queued":
                item["state"] = "not-requested"
                item["why"] = batch["stopWhy"]


def _revalidate(batch: dict) -> tuple[str, str] | None:
    """Has the candidate, the rule or the checkout moved since the batch
    began? Read again, before every request."""
    head = gates.head_for(batch["task"], batch["scope"])
    if head.get("commit") != batch["head"]:
        return ("source-drift",
                f"{STOPS['source-drift']} (it was {batch['head'][:12]}, it "
                f"is now {str(head.get('commit') or 'gone')[:12]})")
    if gates.gate_policy().get("revision") != batch["policyRevision"]:
        return ("policy-changed", None)
    if dirty(batch["ref"]):
        return ("dirty", None)
    return None


def _row_now(batch: dict, item: dict) -> dict | None:
    decision = gates.evaluate(batch["task"], batch["scope"])
    return next((r for r in decision.get("requirements") or []
                 if r.get("kind") == "check" and r.get("check") == item["check"]
                 and (r.get("scope") or None) == (item.get("scope") or None)),
                None)


def _run(batch: dict) -> None:
    try:
        for item in batch["items"]:
            if item["state"] != "queued":
                continue
            if batch.get("cancel"):
                _halt(batch, "cancelled")
                break
            moved = _revalidate(batch)
            if moved:
                _halt(batch, *moved)
                break
            # The requirement read again: an agent's request, or another
            # person's ↻, may have answered it while this one waited.
            verify.forget_cached()
            gates.forget_cached()
            row = _row_now(batch, item)
            if row is None:
                _halt(batch, "policy-changed")
                break
            now = _classify(row, {item["check"]: True})
            if now["state"] != "request":
                with _LOCK:
                    item.update(state=now["state"], reason=now["reason"],
                                evidence=now.get("evidence"),
                                why=now.get("why"))
                _save(batch)
                continue
            with _LOCK:
                item.update(state="requesting", started=time.time())
            _save(batch)
            record = verify.request(check=item["check"], task=batch["task"],
                                    scope=item.get("scope"), intent=INTENT,
                                    stage=batch["stage"], actor=batch["actor"])
            source = (record.get("source") or {}).get("commit")
            with _LOCK:
                item["evidence"] = record.get("id")
                item["ended"] = time.time()
                item["outcome"] = record.get("outcome")
                item["reason"] = record.get("reason")
                item["why"] = record.get("why")
                if record.get("duplicate"):
                    item["state"] = "joined"
                    item["why"] = ITEM_STATES["joined"]
                elif source and source != batch["head"]:
                    item["state"] = "other-candidate"
                    item["why"] = (f"{ITEM_STATES['other-candidate']} "
                                   f"({source[:12]}, not "
                                   f"{batch['head'][:12]})")
                elif record.get("reused"):
                    item["state"] = "reused"
                else:
                    item["state"] = record.get("outcome") or "error"
            _save(batch)
            if item["state"] == "other-candidate":
                _halt(batch, "source-drift")
                break
            if batch.get("cancel"):
                _halt(batch, "cancelled")
                break
    except Exception as exc:                 # noqa: BLE001 — see below
        # The record must close whatever happened; a batch that reads as
        # running for ever is the one thing this module must not leave.
        with _LOCK:
            for item in batch["items"]:
                if item["state"] == "requesting":
                    item["state"] = "error"
                    item["why"] = f"bench could not request it: {exc}"
        _halt(batch, "runner-error", f"{STOPS['runner-error']}: {exc}")
    finally:
        with _LOCK:
            batch["state"] = "stopped" if batch.get("stop") else "finished"
            batch["ended"] = time.time()
            ACTIVE.pop(batch["task"], None)
        _store(batch)
        state.record_board_event({
            "kind": "verify", "actor": "board", "file": batch["task"],
            "summary": line(batch)})
        state.broadcast({"type": "verify"})


def cancel(filename: str) -> dict:
    """Stop a batch: the check in flight is cancelled through verify's own
    cancel (recorded `cancelled`, never a failure), and the checks not yet
    reached are listed as not requested. Nothing is retried."""
    with _LOCK:
        batch = ACTIVE.get(filename)
        if batch is None:
            raise Refused("no-batch")
        batch["cancel"] = True
        current = next((i for i in batch["items"]
                        if i["state"] == "requesting"), None)
    if current is not None:
        live = _live(filename, current)
        if live.get("evidence"):
            try:
                verify.cancel(live["evidence"])
            except ValueError:
                pass                         # it ended on its own meanwhile
    return public(latest(filename))


def recover() -> list[dict]:
    """What a restarted board owes a batch an earlier board was running:
    closed as stopped, the check in flight read as *no result — the board
    restarted*, the rest not requested. Nothing is re-requested."""
    closed = []
    folder = dir_for()
    if not folder.is_dir():
        return closed
    for path in sorted(folder.glob("*.json")):
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if batch.get("state") != "running":
            continue
        with _LOCK:
            if batch.get("task") in ACTIVE:
                continue
        for item in batch.get("items") or []:
            if item.get("state") == "requesting":
                item.update(state="error", reason="board-restart",
                            why="no result — the board restarted")
        _halt(batch, "board-restart")
        batch.update(state="stopped", ended=time.time())
        closed.append(_store(batch))
    return closed


# ── what a reader is shown ─────────────────────────────────────────────


def _live(filename: str, item: dict) -> dict:
    """The verification in flight for one item, from verify's registry —
    so the page can say *preparing* apart from *running*."""
    key = verify._key(filename, item["check"], item.get("scope"))
    with state.LOCK:
        evidence = verify._KEYS.get(key)
        record = verify.RUNNING.get(evidence) if evidence else None
        outcome = (record or {}).get("outcome")
    return {"evidence": evidence if record else None, "outcome": outcome}


DONE = ("met", "joined", "reused", "problem", "unavailable", "not-requested",
        "other-candidate", "passed", "failed", "prepare-failed", "denied",
        "cancelled", "error")

PUBLIC_ITEM = ("check", "scope", "name", "coverage", "state", "reason",
               "outcome", "evidence", "why", "remedy", "plain", "started",
               "ended")


def public(batch: dict | None) -> dict | None:
    """A batch as the page reads it: an allowlist, with the item in flight
    read live (preparing or running) and the counts a progress line
    needs."""
    if not batch:
        return None
    out = {k: batch.get(k) for k in (
        "v", "id", "task", "stage", "scope", "head", "ref", "intent", "actor",
        "requested", "state", "ended", "stop", "stopWhy", "location",
        "joinedBatch") if k in batch}
    items = []
    for item in batch.get("items") or []:
        shown = {k: item.get(k) for k in PUBLIC_ITEM if k in item}
        if item.get("state") == "requesting" and batch.get("state") == "running":
            live = _live(batch["task"], item)
            shown["live"] = live["outcome"] or "starting"
            shown["evidence"] = shown.get("evidence") or live["evidence"]
        shown["words"] = (verify.OUTCOMES.get(item.get("state"))
                          or ITEM_STATES.get(item.get("state"))
                          or item.get("state"))
        items.append(shown)
    out["items"] = items
    out["summary"] = summary(batch)
    return out


def summary(batch: dict | None) -> dict | None:
    """How far a batch has got: of the checks it set out to request, how
    many have an answer, which one is in flight and which remain."""
    if not batch:
        return None
    asked = [i for i in batch.get("items") or [] if i.get("asked")]
    finished = [i for i in asked if i.get("state") not in ("queued",
                                                          "requesting")]
    current = next((i for i in asked if i.get("state") == "requesting"), None)
    return {"state": batch.get("state"), "head": batch.get("head"),
            "total": len(asked), "done": len(finished),
            "current": _label(current) if current else None,
            "remaining": [_label(i) for i in asked
                          if i.get("state") == "queued"],
            "stop": batch.get("stop")}


def line(batch: dict) -> str:
    """The ticker's sentence for a batch's ending."""
    said = []
    for item in batch["items"]:
        if item["state"] in ("met", "unavailable", "problem"):
            continue
        said.append(f"{_label(item)} {item['state']}")
    head = str(batch.get("head") or "")[:12]
    tail = f" — stopped: {batch['stopWhy']}" if batch.get("stop") else ""
    return (f"required checks on {batch['task']} at {head}: "
            f"{', '.join(said) or 'nothing requested'}{tail}")
