"""Refining a card on the board: comments, refinement jobs, and the
explicit Apply or Discard of what a job proposes (task 65).

Three things a person can do, and they are deliberately three:

- **Add comment** saves text to the card's discussion. Nothing else: no
  agent is asked anything because a comment exists, and nothing ever reads
  an old comment as a fresh instruction.
- **Update card with agent** asks for one refinement of one comment (a new
  one written in the same breath, or one already on the card). It launches
  the `refine` intent — no worktree, no command, no edit tool, no shell —
  which reads the card and the code and proposes a new brief on stdout.
  Bench validates the proposal and keeps it; the brief is not touched.
- **Apply** or **Discard** decides one proposal. Apply replaces the brief
  through `taskfiles.replace_brief` — one write, one work-state commit —
  and Discard records that it was declined. Both leave a decision in the
  card's discussion, so the outcome travels with the card.

What this module keeps is runtime state, and it says so: a job's status,
the proposal text and the diff live under `state/refine/`, machine-local
like the plans and manifests beside them. They are never a second source
of requirements. The brief, the comments and the decisions are in the
task file, and the task file is what syncs. A proposal nobody decided is a
suggestion on one machine, and it is shown as one.

Refinement never implements. Nothing here creates a branch or a worktree,
moves a card, retargets a recorded plan, starts a work run or talks to
GitHub, and an active implementation run on the card blocks refining and
applying — while a comment may always be saved.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

import agents
import config
import phases
import profiles
import state
import taskfiles

VERSION = 1

# queued → running → proposed → applied | discarded | superseded
#                  ↘ failed | cancelled
STATUSES = ("queued", "running", "proposed", "failed", "cancelled",
            "applied", "discarded", "superseded")
ACTIVE = {"queued", "running"}
OPEN = ACTIVE | {"proposed"}

# The runs that mutate a card's work. Refining the brief under one of them
# would change requirements an agent is already working to, without it
# knowing — so refining and applying wait, and the person is pointed at the
# existing hold. Nothing here stops an agent itself.
MUTATING_MODES = {"work", "act-pr"}

MAX_EXPLANATION = 4000

LOCK = threading.RLock()
JOBS: dict[str, dict] = {}
_loaded = False


class Conflict(ValueError):
    """The request is valid; the job's state has already been decided."""


# ── persistence ────────────────────────────────────────────────────────


def _path(job_id: str) -> Path:
    return config.REFINE_DIR / f"{job_id}.json"


def _save(job: dict) -> None:
    config.REFINE_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(job["id"])
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(job, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _load() -> None:
    """Read every job on disk once. A file that does not parse is left
    where it is and skipped, rather than taken for an empty job."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not config.REFINE_DIR.is_dir():
        return
    for path in sorted(config.REFINE_DIR.glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(job, dict) and job.get("id"):
            JOBS.setdefault(job["id"], job)


def reset() -> None:
    """Forget what is in memory, so the next read comes from disk — what a
    restart does, and what tests use to play one."""
    global _loaded
    with LOCK:
        JOBS.clear()
        _loaded = False


def recover() -> int:
    """At board start: a job recorded as queued or running belongs to a
    process this board no longer has. It is closed as failed, naming the
    restart — never re-run, because asking again is a person's decision and
    an automatic replay is exactly what must not happen. A proposal waiting
    for a decision survives a restart unchanged."""
    closed = 0
    with LOCK:
        _load()
        for job in JOBS.values():
            if job.get("status") in ACTIVE:
                job.update({"status": "failed", "ended": time.time(),
                            "error": ("the board restarted while this "
                                      "refinement ran, so its result was "
                                      "lost — nothing was applied. Retry it "
                                      "when you are ready")})
                _save(job)
                closed += 1
    return closed


# ── reading ────────────────────────────────────────────────────────────


def _card_path(filename: str, stage: str) -> Path:
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    if stage not in config.STAGE_DIRS:
        raise ValueError("unknown stage")
    path = config.TASKS / stage / filename
    if not path.is_file():
        raise ValueError(f"{filename} is not in {stage}/ — refresh the board")
    return path


def _jobs_for(filename: str, comment: str | None = None) -> list[dict]:
    return sorted((j for j in JOBS.values() if j.get("task") == filename
                   and (comment is None or j.get("comment") == comment)),
                  key=lambda j: (j.get("created") or 0, j["id"]))


def freshness(job: dict) -> dict:
    """Whether a proposal still describes the card it would be applied to.
    Compared, never repaired: a stale proposal is regenerated, not merged."""
    stage = taskfiles.find_stage_of(job["task"])
    if stage is None:
        return {"fresh": False, "why": f"{job['task']} is no longer on the board"}
    if stage != job.get("stage"):
        return {"fresh": False, "why": (f"the card moved from {job.get('stage')}/ "
                                        f"to {stage}/ since this was proposed")}
    try:
        text = (config.TASKS / stage / job["task"]).read_text(encoding="utf-8")
    except OSError:
        return {"fresh": False, "why": "the card could not be read"}
    if taskfiles.brief_sha(taskfiles.card_parts(text)["brief"]) != job.get("briefSha"):
        return {"fresh": False, "why": "the brief has changed since this was "
                                       "proposed — someone edited it, a sync "
                                       "brought a change, or another proposal "
                                       "was applied"}
    if job.get("comment") and taskfiles.find_entry(text, job["comment"]) is None:
        return {"fresh": False, "why": "the comment it answers is no longer on "
                                       "the card"}
    return {"fresh": True, "why": None}


PUBLIC = ("v", "id", "task", "stage", "comment", "attempt", "retryOf", "status",
          "created", "started", "ended", "agent", "name", "error", "refused",
          "explanation", "diff", "sections", "affected", "noChange",
          "proposedBrief", "decidedBy", "decidedAt", "committed", "request")


def public(job: dict) -> dict:
    out = {key: job[key] for key in PUBLIC if key in job}
    if job.get("status") == "proposed":
        out["fresh"] = freshness(job)
    return out


def blocking_run(filename: str) -> str | None:
    """Why the brief of this card may not be refined or applied right now,
    or None. Read from the processes themselves, so a run that died cannot
    lock the card. Saving a comment never asks this."""
    for run in agents.working_on({filename}):
        mode = run.get("mode") or "work"
        name = run.get("name") or "an agent"
        if mode in MUTATING_MODES:
            return (f"{name} is implementing {filename} ({mode}) — the brief is "
                    f"not changed under a running implementation. Comments can "
                    f"still be added. To refine the brief now, hold the run "
                    f"with ‖ hold first; nothing is stopped automatically, and "
                    f"the running agent does not see new comments")
        if mode == "refine":
            return (f"{name} is already refining {filename} — wait for its "
                    f"proposal, or cancel it")
        return (f"{name} is running a {mode} on {filename}, and a card runs one "
                f"agent at a time — wait for it to finish")
    if filename in state.completing_public():
        return (f"{filename} is being merged and cleaned up — refine it once "
                f"that has finished")
    try:
        phases.assert_not_working(filename, "refine its brief")
    except ValueError as exc:
        return str(exc)
    return None


def readiness() -> dict:
    """Whether this board can launch a refinement at all, resolved through
    the same resolver a launch rides — and nothing launched to find out."""
    found = profiles.resolve("refine")
    return {"ready": not found["blockers"],
            "blockers": list(found["blockers"]),
            "codes": [d["code"] for d in found.get("diagnostics") or []]}


def for_card(filename: str, stage: str) -> dict:
    """Everything the card's discussion panel shows, in one read: the
    entries off the card itself, the jobs this board holds for it, and why
    refining is not available when it is not."""
    path = _card_path(filename, stage)
    text = path.read_text(encoding="utf-8")
    parts = taskfiles.card_parts(text)
    with LOCK:
        _load()
        jobs = [public(j) for j in reversed(_jobs_for(filename))]
    return {
        "file": filename, "stage": stage,
        "entries": taskfiles.discussion(text),
        "brief": parts["brief"],
        "briefSha": taskfiles.brief_sha(parts["brief"]),
        "jobs": jobs,
        "blocked": blocking_run(filename),
        "refine": readiness(),
        # what saving does here, said rather than implied: a comment is
        # written to the card, committed only under BOARD_COMMIT_MOVES and
        # published only under BOARD_SYNC
        "persistence": {"commits": bool(config.COMMIT_MOVES),
                        "syncs": bool(getattr(config, "SYNC", False)),
                        "stateRepo": config.state_repo() is not None},
        "local": "proposals and job status are local to this board until "
                 "applied or discarded; comments and decisions are in the card",
    }


def board() -> dict:
    """Per card, the newest undecided job — what the card face needs to
    say a refinement is running or a proposal is waiting. Empty on a board
    that has refined nothing."""
    out: dict = {}
    with LOCK:
        _load()
        for job in sorted(JOBS.values(), key=lambda j: j.get("created") or 0):
            if job.get("status") in OPEN or job.get("status") == "failed":
                out[job["task"]] = {"id": job["id"], "status": job["status"]}
            elif job["task"] in out:
                del out[job["task"]]
    return out


# ── adding a comment ───────────────────────────────────────────────────


def _author() -> str:
    return taskfiles.actor_name() or "unattributed"


def comment(filename: str, stage: str, body) -> dict:
    """Save a comment. Allowed whatever is running on the card, and it
    starts nothing."""
    _card_path(filename, stage)
    entry = taskfiles.add_comment(filename, stage, body, _author())
    state.record_board_event({
        "kind": "agent", "actor": "you", "file": filename,
        "summary": (f"{filename}: comment {entry['id']} added by {entry['author']}"
                    + ("" if entry["committed"] else
                       " (written to the card, not committed — "
                       "BOARD_COMMIT_MOVES is off)"))})
    state.broadcast({"type": "board"})
    return entry


# ── asking for a refinement ────────────────────────────────────────────


def _discussion_text(entries: list[dict], chosen: str) -> str:
    lines = []
    for entry in entries:
        who = entry.get("author") or "unattributed"
        if entry.get("kind") == "comment":
            mark = "  ← the instruction" if entry["id"] == chosen else ""
            lines.append(f"[{entry['id']} · {who} · {entry.get('at', '')}]{mark}\n"
                         f"{entry.get('body', '')}")
        elif entry.get("kind") == "decision":
            lines.append(f"[{entry['id']} · {entry.get('outcome')} "
                         f"{entry.get('proposal')} for {entry.get('comment')}]\n"
                         f"{entry.get('body', '')}")
    return "\n\n".join(lines) or "(none)"


def request(filename: str, stage: str, *, comment_id: str | None = None,
            text=None, request_key: str | None = None,
            retry: bool = False) -> dict:
    """Ask for one refinement of one comment.

    Exactly one of `comment_id` (a comment already on the card) and `text`
    (a new instruction, saved as a comment first) names the instruction.
    `request_key` is the page's identity for the click: the same key twice
    is the same request, answered with the job it already made — a double
    click, a resubmitted form or a retried request never launches twice.

    A comment that already has an open job gets that job back. One whose
    last job ended (failed, cancelled, applied or discarded) is asked again
    only with `retry`, which is what makes a deliberate retry different
    from an accidental replay. Nothing is written — no comment, no job, no
    pinned input — when the request is refused.
    """
    path = _card_path(filename, stage)
    key = str(request_key or "").strip()[:80] or None
    if (comment_id is None) == (text is None):
        raise ValueError("name the instruction: a comment on the card, or the "
                         "text of a new one — not both")
    if text is not None:
        problem = taskfiles.comment_problem(text)
        if problem:
            raise ValueError(problem)

    with LOCK:
        _load()
        if key:
            seen = next((j for j in JOBS.values() if j.get("request") == key), None)
            if seen is not None:
                return {"job": public(seen), "duplicate": True}
        blocked = blocking_run(filename)
        if blocked:
            raise ValueError(blocked)
        card = path.read_text(encoding="utf-8")
        if comment_id is not None:
            entry = taskfiles.find_entry(card, str(comment_id))
            if entry is None or entry.get("kind") != "comment":
                raise ValueError(f"{comment_id} is not a comment on {filename} — "
                                 f"refresh the card")
            prior = _jobs_for(filename, entry["id"])
            last = prior[-1] if prior else None
            if last is not None and last["status"] in ACTIVE:
                return {"job": public(last), "duplicate": True}
            if last is not None and last["status"] == "proposed" and not retry:
                return {"job": public(last), "duplicate": True}
            if last is not None and not retry:
                raise Conflict(
                    f"comment {entry['id']} was refined already — its last "
                    f"attempt was {last['status']}. Choose Retry to ask again "
                    f"deliberately")
        else:
            prior, last = [], None

        # resolved and pinned before anything is written: a board that
        # cannot launch a refinement refuses here, with no comment saved
        manifest = profiles.require(profiles.manifest(
            "refine", filename, stage, pin_inputs=True))

        if text is not None:
            entry = taskfiles.add_comment(filename, stage, text, _author())
            card = path.read_text(encoding="utf-8")
        if last is not None and last["status"] == "proposed":
            last.update({"status": "superseded", "ended": time.time()})
            _save(last)

        parts = taskfiles.card_parts(card)
        job = {
            "v": VERSION, "id": f"p-{secrets.token_hex(5)}",
            "task": filename, "stage": stage, "comment": entry["id"],
            "attempt": len(prior) + 1,
            "retryOf": last["id"] if last is not None else None,
            "request": key or f"auto-{secrets.token_hex(6)}",
            "status": "queued", "created": time.time(),
            "briefSha": taskfiles.brief_sha(parts["brief"]),
            "briefBefore": parts["brief"],
        }
        JOBS[job["id"]] = job
        _save(job)
        try:
            _launch(job, card, entry, manifest)
        except ValueError as exc:
            job.update({"status": "failed", "ended": time.time(),
                        "error": f"the refinement could not be launched: {exc}"})
            _save(job)
            raise
        return {"job": public(job), "duplicate": False,
                "comment": {k: entry.get(k) for k in
                            ("id", "author", "at", "committed", "written")}}


def _launch(job: dict, card: str, entry: dict, manifest: dict) -> None:
    filename, stage = job["task"], job["stage"]
    agent_id = (f"refine-{filename[:-3]}-{time.strftime('%H%M%S')}-"
                f"{secrets.token_hex(2)}")
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = profiles.record(agent_id, manifest)
    fields = config.prompt_fields(filename, stage)
    prompt = config.prompt("refine.md").format(
        filename=filename, stage=stage, body=card, brief=job["briefBefore"],
        discussion=_discussion_text(taskfiles.discussion(card), entry["id"]),
        comment_id=entry["id"], comment_author=entry.get("author") or "unattributed",
        instruction=entry.get("body", ""),
        guidance_read_only=fields["guidance_read_only"],
        task_path=fields["task_path"],
        context=profiles.prompt_block(manifest))
    proc, log_file, model = agents._launch("refine", prompt, config.REPO,
                                           agent_id, filename, log_path, manifest)
    name = agents._pick_name(filename)
    record = {
        "id": agent_id, "task": filename, "branch": None, "worktree": None,
        "base": None, "status": "running", "rc": None, "started": time.time(),
        "session": None, "log": str(log_path), "proc": proc,
        "origin": stage, "mode": "refine", "name": name, "model": model or None,
        "profile": profiles.summary(manifest), "refineJob": job["id"],
    }
    with state.LOCK:
        state.AGENTS[agent_id] = record
    job.update({"status": "running", "started": time.time(), "agent": agent_id,
                "name": name})
    _save(job)
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": (f"{name} is refining {filename} from comment {entry['id']} "
                    f"— it proposes; nothing changes until you apply it")})
    threading.Thread(target=_reap, args=(job["id"], agent_id, proc, log_file),
                     daemon=True).start()


# ── what came back ─────────────────────────────────────────────────────

PROPOSAL_RE = re.compile(
    r"<<<EXPLANATION[ \t]*\r?\n(?P<explanation>.*?)\r?\nEXPLANATION>>>.*?"
    r"<<<BRIEF[ \t]*\r?\n(?P<brief>.*?)\r?\nBRIEF>>>", re.DOTALL)
DECLINED_RE = re.compile(r"^REFINEMENT DECLINED:\s*(.*)$", re.MULTILINE)


def parse(log: str) -> dict:
    """The run's answer, read off its output — the last proposal block, or
    the last decline. Anything else is no answer at all."""
    marker = log.rfind("REFINEMENT PROPOSAL")
    if marker >= 0:
        found = list(PROPOSAL_RE.finditer(log, marker))
        if found:
            last = found[-1]
            return {"kind": "proposal",
                    "explanation": last.group("explanation").strip(),
                    "brief": last.group("brief").strip("\n") + "\n"}
    declines = [m.group(1).strip() for m in DECLINED_RE.finditer(log)
                if not m.group(1).strip().startswith("<")]
    if declines:
        return {"kind": "declined", "reason": declines[-1] or "no reason given"}
    return {"kind": "none"}


def validate(job: dict, answer: dict) -> dict:
    """Server-side: what a proposal may become. The run was told the rules;
    this is where they are enforced, against the brief the run actually
    read rather than whatever the card says now."""
    if answer["kind"] == "declined":
        return {"status": "failed",
                "error": f"the agent declined to refine the brief: {answer['reason']}"}
    if answer["kind"] != "proposal":
        return {"status": "failed",
                "error": ("the run ended without a proposal in the required "
                          "shape, so nothing was proposed and nothing changed")}
    explanation = answer["explanation"].strip()
    if not explanation:
        return {"status": "failed",
                "error": "the proposal came without an explanation, so it was refused"}
    problems = taskfiles.brief_problems(job["briefBefore"], answer["brief"])
    if problems:
        return {"status": "failed", "refused": problems,
                "error": f"the proposal was refused: {problems[0]}"}
    before, after = job["briefBefore"], answer["brief"]
    return {
        "status": "proposed",
        "explanation": explanation[:MAX_EXPLANATION],
        "proposedBrief": after,
        "diff": taskfiles.brief_diff(before, after),
        "sections": taskfiles.changed_sections(before, after),
        "affected": taskfiles.affected_criteria(before, after),
        "noChange": taskfiles.brief_sha(before) == taskfiles.brief_sha(after),
    }


def _reap(job_id: str, agent_id: str, proc, log_file) -> None:
    record, stopped, rc = agents._finish(agent_id, proc, log_file)
    with LOCK:
        job = JOBS[job_id]
        if stopped:
            job.update({"status": "cancelled",
                        "error": "cancelled — nothing was proposed or applied"})
        elif rc != 0:
            job.update({"status": "failed",
                        "error": f"the run exited rc={rc}: {agents._why(record)}"})
        else:
            try:
                log = Path(record["log"]).read_text(encoding="utf-8",
                                                    errors="replace")
            except OSError:
                log = ""
            job.update(validate(job, parse(log)))
        job["ended"] = time.time()
        _save(job)
        status, filename = job["status"], job["task"]
        name = job.get("name") or "the agent"
        summary = {
            "proposed": (f"{name} proposed a refinement of {filename} — review "
                         f"the diff, then apply or discard it"
                         + (" (it proposes no change)" if job.get("noChange") else "")),
            "cancelled": f"{name}'s refinement of {filename} was cancelled",
        }.get(status, f"{name}'s refinement of {filename} failed — {job.get('error')}")
    state.record_board_event({"kind": "agent", "actor": "agent", "file": filename,
                              "summary": summary[:300]})
    state.broadcast({"type": "board"})
    state.broadcast({"type": "agents"})


def cancel(job_id: str) -> dict:
    with LOCK:
        _load()
        job = JOBS.get(job_id)
        if job is None:
            raise ValueError("unknown refinement")
        if job["status"] not in ACTIVE or not job.get("agent"):
            raise ValueError(f"the refinement is {job['status']}, not running")
        agent_id = job["agent"]
    agents.stop_agent(agent_id)
    return public(job)


# ── deciding ───────────────────────────────────────────────────────────


def _decided(job: dict, card: str) -> dict | None:
    """A decision already in the card — made by this board before a
    restart, or by a replica whose commit a sync brought — is the answer,
    and the job is brought into line with it rather than decided twice."""
    found = taskfiles.decision_for(card, job["id"])
    if found is None:
        return None
    job.update({"status": "applied" if found.get("outcome") == "applied"
                else "discarded",
                "decidedBy": found.get("author"), "decidedAt": found.get("at")})
    _save(job)
    return found


def apply(job_id: str) -> dict:
    """Apply one proposal: the brief replaced, the decision recorded, one
    commit. Safe to repeat — a second Apply answers with what the first
    did — and mutually exclusive with Discard under one lock."""
    actor = _author()
    with LOCK:
        _load()
        job = JOBS.get(job_id)
        if job is None:
            raise ValueError("unknown refinement")
        if job["status"] == "applied":
            return {"job": public(job), "already": True}
        if job["status"] == "discarded":
            raise Conflict("this proposal was discarded, so it cannot be "
                           "applied — ask for a new refinement")
        if job["status"] != "proposed":
            raise Conflict(f"there is nothing to apply — the refinement is "
                           f"{job['status']}")
        filename = job["task"]
        blocked = blocking_run(filename)
        if blocked:
            raise ValueError(blocked)
        stage = taskfiles.find_stage_of(filename)
        if stage is not None:
            card = (config.TASKS / stage / filename).read_text(encoding="utf-8")
            if _decided(job, card):
                return {"job": public(job), "already": True}
        fresh = freshness(job)
        if not fresh["fresh"]:
            raise taskfiles.StaleCard(f"{fresh['why']} — nothing was applied; "
                                      f"regenerate the proposal")
        at = taskfiles.now_iso()
        new_sha = taskfiles.brief_sha(job["proposedBrief"])
        decision = {"v": taskfiles.DISCUSSION_VERSION,
                    "id": f"d-{secrets.token_hex(5)}", "kind": "decision",
                    "outcome": "applied", "proposal": job["id"],
                    "comment": job["comment"], "author": taskfiles._one_line(actor),
                    "at": at, "briefBefore": job["briefSha"][:12],
                    "briefAfter": new_sha[:12], "sections": job.get("sections") or [],
                    "affected": job.get("affected") or []}
        sections = ", ".join(job.get("sections") or []) or "no section"
        body = (f"Applied the proposal for {job['comment']}. Changed: {sections}."
                + (f" Acceptance records marked for recheck: "
                   f"{', '.join(job['affected'])}." if job.get("affected") else "")
                + f"\n\n{job.get('explanation') or ''}")
        result = taskfiles.replace_brief(
            filename, job["stage"], expected_sha=job["briefSha"],
            new_brief=job["proposedBrief"], decision=decision,
            title=f"Applied {job['id']} — {decision['author']} · {taskfiles.stamp(at)}",
            body=body, affected=job.get("affected") or [])
        job.update({"status": "applied", "decidedBy": decision["author"],
                    "decidedAt": at, "committed": result["committed"]})
        _save(job)
    state.record_board_event({
        "kind": "agent", "actor": "you", "file": filename,
        "summary": (f"{filename}: brief refined from comment {job['comment']} "
                    f"({sections}) — applied by {decision['author']}"
                    + ("" if result["committed"] else
                       " (written, not committed — BOARD_COMMIT_MOVES is off)"))})
    state.broadcast({"type": "board"})
    return {"job": public(job), "already": False}


def discard(job_id: str) -> dict:
    """Decline one proposal. The brief is untouched; the decision is
    recorded in the card's discussion beside the comment it answered."""
    actor = _author()
    with LOCK:
        _load()
        job = JOBS.get(job_id)
        if job is None:
            raise ValueError("unknown refinement")
        if job["status"] == "discarded":
            return {"job": public(job), "already": True}
        if job["status"] == "applied":
            raise Conflict("this proposal was applied, so it cannot be discarded "
                           "— the brief already holds it")
        if job["status"] != "proposed":
            raise Conflict(f"there is nothing to discard — the refinement is "
                           f"{job['status']}")
        filename = job["task"]
        stage = taskfiles.find_stage_of(filename)
        if stage is None:
            raise ValueError(f"{filename} is no longer on the board")
        card = (config.TASKS / stage / filename).read_text(encoding="utf-8")
        if _decided(job, card):
            return {"job": public(job), "already": True}
        at = taskfiles.now_iso()
        decision = {"v": taskfiles.DISCUSSION_VERSION,
                    "id": f"d-{secrets.token_hex(5)}", "kind": "decision",
                    "outcome": "discarded", "proposal": job["id"],
                    "comment": job["comment"], "author": taskfiles._one_line(actor),
                    "at": at, "briefBefore": job["briefSha"][:12]}
        result = taskfiles.add_discussion_entry(
            filename, stage, decision,
            f"Discarded {job['id']} — {decision['author']} · {taskfiles.stamp(at)}",
            (f"Discarded the proposal for {job['comment']}; the brief was not "
             f"changed.\n\n{job.get('explanation') or ''}"),
            f"proposal {job['id']} discarded")
        job.update({"status": "discarded", "decidedBy": decision["author"],
                    "decidedAt": at, "committed": result["committed"]})
        _save(job)
    state.record_board_event({
        "kind": "agent", "actor": "you", "file": filename,
        "summary": f"{filename}: proposal {job['id']} discarded — the brief is unchanged"})
    state.broadcast({"type": "board"})
    return {"job": public(job), "already": False}
