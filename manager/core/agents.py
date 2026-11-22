"""Headless Claude Code agents: launching, reaping, stopping, diffing.

Two kinds:
- work agents (start_agent) get an isolated git worktree + branch and may
  edit and commit; the board moves their card in-progress → testing.
- review agents (start_review) are read-only, run in the main checkout, and
  their relevance report is appended to the task file by the board.

Every git command here runs against the **application** repository —
asked through `plans.app_repo()`, which is `config.REPO` — and every
worktree under `config.WORKTREES`. The task file the board writes
afterwards may live in a different repository entirely (task 56);
`taskfiles` owns that half.

Which branch a launch starts from and what its PR targets are not decided
here either: `plans` resolves and validates one execution plan before any
branch, worktree, claim or process exists, and everything after the
launch reads that record (task 58).

Nor, for a PR review, is *which revision* it reads. `reviews` resolves
the pull request's repository, base and head, brings that commit here and
adds the run-owned detached checkout the review runs in — so a review
executes the proposed code in a checkout bench made for it rather than in
the live application, and its verdict is bound to a commit (task 69).

Nor is what a run may *do* in that checkout. `profiles` resolves one
execution profile per launch intent — the named command grants, the
approved roots, the readable inputs it pins, and the capabilities that
are explicitly denied — validates it in the same breath as the plan, and
records the manifest the run actually rode (task 67). Every launch below
therefore refuses twice before it spends a token: once on the plan, once
on the profile.

Prompts live in .prompts/ and are read fresh on every launch.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import config
import events
import evidence
import gatepolicy
import gates
import githost
import gitrepo
import inputs
import operations
import plans
import profiles
import progress
import records
import reportfiling
import reports
import results
import reviews
import state
import taskfiles
import templates
import verify
from taskfiles import (actor_name, collect, commit_edit,
                       find_stage_of, move_task, read_task, set_assignee)

# A phase runs on an integration branch of its own — phases.py owns the
# behaviour, the name lives here because this is where a launch decides
# what to branch from.
PHASE_BRANCH_PREFIX = config.PHASE_BRANCH_PREFIX


def phase_branch(phase_file: str) -> str:
    return PHASE_BRANCH_PREFIX + phase_file[:-3]


def _report_of(record: dict, text: str | None = None) -> str:
    """What the record keeps of a run's report: cleaned, and clipped by
    reports.report — head first, because the report's own first line is
    where it says what happened. `text` names the part after a marker when
    there is one; otherwise the whole log."""
    if text is None:
        try:
            text = Path(record["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return reports.report(text, log_path=record.get("log"))


def _file_report(record: dict, heading: str, report: str,
                 provenance: dict | None = None) -> None:
    """The report travels with the task, like every review does — and, like
    every other board-made write to a task file, it reaches git rather than
    sitting modified in one working tree (task 44).

    Filed on the board the run was launched from (`record["tasks"]`), in
    the stage the card is in *now* — a card moved during the run gets its
    report where it went — or not at all (task 148). The stage is looked up
    on that board and the append is bound to it, so a lookup on one board
    can no longer become a write on another, and a card that has gone is
    said to have gone rather than recreated to hold the report.

    Since task 169 the filing is a durable operation (`reportfiling`): its
    heading — a review's identity — is allocated once and kept with the
    exact block before the first append, so a report that could not be
    filed is retained and a person's retry files that same report, under
    that same heading, on that same card. A review carries what bench knows
    it was *of* (task 168) on one line under its heading that only the
    board writes; the run's own lines shaped like it were quoted by
    `reports.defuse` before reaching here."""
    reportfiling.file(record, heading, report, provenance)


def _session_report(record: dict, report: str) -> None:
    """And it lands in the session's timeline as its closing entry."""
    if not report or not record.get("session"):
        return
    events.ingest_event({
        "v": 1, "session": record["session"], "kind": "report",
        "summary": f"{record.get('name') or 'the agent'}'s report on {record['task']}",
        "detail": report, "agent": record["id"], "task": record["task"],
        # the board's own event, so it carries the board's own identity: one
        # closing report per run, however often this is reached
        "id": f"board:report:{record['id']}",
    })


# Short coastal names in the Bench spirit — one per running agent, so the
# board reads "Wren is on #09", not "agent 09-application-layer-091203".
NAMES = ["Wren", "Juno", "Basil", "Piper", "Sage", "Reed", "Olive", "Finch",
         "Hazel", "Cleo", "Milo", "Fern", "Ada", "Otto", "Nell", "Skye"]


def pick_name(stem: str) -> str:
    """Stable-ish per task (same task tends to get the same name back),
    skipping names already worn by a running agent."""
    with state.LOCK:
        used = {r.get("name") for r in state.AGENTS.values() if r["status"] == "running"}
    start = sum(ord(c) for c in stem) % len(NAMES)
    for i in range(len(NAMES)):
        name = NAMES[(start + i) % len(NAMES)]
        if name not in used:
            return name
    return NAMES[start]


# The thread that sees each run end, by run. A reaper writes after the
# status leaves `running` — the closing report, the move, the PR — so a
# caller that must know those writes are over (a test about to restore the
# board's configuration, task 148) joins the reaper rather than polling
# the status.
REAPERS: dict[str, threading.Thread] = {}


def _start_reaper(target, agent_id: str, proc, log_file) -> None:
    thread = threading.Thread(target=target, args=(agent_id, proc, log_file),
                              daemon=True, name=f"reap:{agent_id}")
    with state.LOCK:
        # The board this run was launched from, bound for its ending: its
        # report is filed there or nowhere (task 148), whatever the
        # process's configuration says by the time the run ends.
        state.AGENTS[agent_id].setdefault("tasks", str(config.TASKS))
        REAPERS[agent_id] = thread
    thread.start()


def join_reapers(timeout: float = 30.0) -> list[str]:
    """Wait for every reaper started so far to finish its run's ending,
    within `timeout` seconds overall. Returns the runs whose reaper is
    still going — empty when every ending has been written. Joined reapers
    are forgotten."""
    deadline = time.monotonic() + timeout
    with state.LOCK:
        pending = list(REAPERS.items())
    alive = []
    for agent_id, thread in pending:
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            alive.append(agent_id)
        else:
            with state.LOCK:
                if REAPERS.get(agent_id) is thread:
                    del REAPERS[agent_id]
    return alive


def _agent_public(record: dict) -> dict:
    public = {k: record[k] for k in
              ("id", "task", "branch", "worktree", "status", "rc", "started", "session")}
    public["mode"] = record.get("mode", "work")
    public["name"] = record.get("name")
    # What this run was allowed to do, as the compact reading a row wears
    # — a count of grants and inputs, and anything that blocked it. The
    # whole manifest is deliberately nowhere near here: it is frozen on
    # disk and fetched by `/api/manifest`, so neither the registry nor the
    # state payload carries a capability table per running agent on every
    # SSE nudge.
    public["profile"] = record.get("profile")
    # For a PR review: which revision it is of and the checkout bench
    # pinned for it. None on every other kind — a relevance check pins
    # nothing, and a work run has its card's own worktree above.
    public["review"] = record.get("review")
    # The model the launch was actually given; None = inherited the
    # vendor's own default. Honesty for the Sessions/Focus views.
    public["model"] = record.get("model")
    public["ended"] = record.get("ended")
    # The outcome a failed run leaves behind, for the card to wear (see
    # _record_failure). None on every run that did not die.
    public["failure"] = record.get("failure")
    # For ↻ act on PR: what it repairs — a card's own branch, or a phase's
    # integration branch and PR (task 174). None on every other kind.
    public["repair"] = record.get("repair")
    return public


def list_public() -> list[dict]:
    with state.LOCK:
        return [_agent_public(a) for a in state.AGENTS.values()]


def _assert_no_running_agent(filename: str) -> None:
    with state.LOCK:
        for record in state.AGENTS.values():
            if record["task"] == filename and record["status"] == "running":
                raise ValueError(f"an agent is already working on {filename}")


def _alive(record: dict) -> bool:
    """Is this run actually running, or only recorded as running?

    The registry's status is flipped by the reaper thread, a moment after
    the process it waits on has gone. Anything that *acts* on a card should
    keep out through that moment — the reaper is about to move the card. A
    rule that only *refuses* must not: a run that died between two reads
    would otherwise lock the card it died on for as long as the board
    lives. So this asks the process rather than the record.
    """
    proc = record.get("proc")
    if proc is None:            # nothing to ask: the registry's word stands
        return True
    try:
        return proc.poll() is None
    except (OSError, ValueError):
        return False


def working_on(files: set[str]) -> list[dict]:
    """The runs alive on these cards, right now — copies, never the
    registry itself. `[]` is "nothing is running here", read from what is
    actually running rather than from what was once started."""
    with state.LOCK:
        records = [dict(record) for record in state.AGENTS.values()
                   if record["task"] in files and record["status"] == "running"]
    return [record for record in records if _alive(record)]


# ── what a phase card may host ─────────────────────────────────────────
#
# A phase card is a list of other cards. Handed to a work agent as a brief
# it reads as a table of contents, and the agent does what it is told —
# which is how one run once implemented two cards at once in a worktree
# nobody was watching. `▸ run phase` already guards its own door (a card
# that is not a phase is refused there); this is the other half of that
# gate, and it is decided kind by kind rather than left to omission:
#
# - **▸ start work** (`start_agent`) refuses. A phase is run with ▸ run
#   phase, which works its list into a branch of its own; its members are
#   worked on their own cards.
# - **↻ act on PR** (`start_pr_fix`) is allowed on a phase card in
#   review/ since task 174 — but on the phase's *integration* target only:
#   its existing `phase/<stem>` branch, its Bench-managed worktree and its
#   open integration PR, resolved by `repair_target` and refused, with the
#   prerequisite named, whenever any of them is not what it should be.
#   Its members are merged already; nothing here reopens one.
# - **◔ still true?** (`start_review`) is allowed. Read-only, no worktree:
#   asking whether a phase is still worth running is a fair question, and
#   the report is appended to the card like any other.
# - **◔ review PR** (`start_pr_review`) is allowed. Read-only, and the PR
#   into `main` is the one thing the whole run exists to produce — the
#   launch just has to name the phase's own branch rather than a
#   `task/<stem>` that was never cut.
#
# It is about *starting*: a card that gains `**Type:** Phase` while an
# ordinary run is in flight is left alone, and the run ends as it would
# have.
PHASE_RUNS_WITH = ("a list of other cards, not a brief. It is queued by "
                   "moving it into To Do: the Orchestrator then cuts a branch "
                   "of its own and works the list into it; its members are "
                   "worked on their own cards")


def is_phase_card(filename: str, stage: str) -> bool:
    """Is this card a phase card? `**Type:** Phase` is the whole of it —
    the same reading `phases._phase_card` refuses a non-phase by, so the
    two gates cannot disagree. A phase card whose list is empty or unwritten
    is still a coordinator, and still no brief for a work agent."""
    try:
        return bool(read_task(config.TASKS / stage / filename, stage)["isPhase"])
    except OSError:
        return False


def validate_launch(filename: str, stage: str, allowed: set[str], why: str | None = None,
              *, phase: str | None = None) -> None:
    """The refusals every launch shares, before anything exists to clean up.

    `phase` is what a phase card should do instead — pass it and this kind
    refuses one, naming that; omit it and the kind is one a phase card may
    host. See the note above for which is which and why.
    """
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    if stage not in allowed:
        raise ValueError(why or f"agents cannot start from {stage}/")
    if not (config.TASKS / stage / filename).is_file():
        raise ValueError(f"{filename} is not in {stage}/ — refresh the board")
    if phase and is_phase_card(filename, stage):
        raise ValueError(f"{filename} is a phase card — {phase}")
    _assert_no_running_agent(filename)


def launch(mode: str, prompt: str, cwd: Path, agent_id: str, filename: str,
            log_path: Path, manifest: dict | None = None):
    """Run one headless job through the configured agent adapter.

    The adapter contract: `run` gets AGENT_PROMPT, AGENT_MODE (the intent:
    work = mutate and commit, act-pr = work + push, review = read-only +
    post PR verdicts), AGENT_COMMANDS (the project's runnable command
    prefixes) and AGENT_MODEL (the configured model, when there is one)
    plus the BOARD_* passthrough for its event bridge; its stdout is the
    job log; exit 0 = completed. Returns (proc, log_file, model) with
    model = '' when the launch inherits the vendor default.

    AGENT_PROFILE and AGENT_CONTEXT are the resolved execution profile's
    half of that contract, and they are *additions*: AGENT_COMMANDS is
    still set to exactly what it always was, so an adapter override that
    has never heard of profiles is handed the same environment it was
    handed before and keeps working. An adapter that does read the profile
    reads it as a narrowing — named grants and denied capabilities — which
    is why passing it cannot broaden what a legacy board grants.
    """
    # Which harness and which model: the manifest's, when there is one,
    # because that is the answer the preflight validated and the record
    # keeps — re-asking `config` here could launch something the record
    # does not say (task 77). With no manifest, the live resolver.
    selection = (manifest or {}).get("selection") or \
        profiles.agent_selection(config.agent_choice(mode))
    harness = selection["harness"]["name"]
    adapter = config.adapter_dir(harness)
    if adapter is None:
        raise ValueError(
            f"agent adapter '{harness}' not found — expected "
            f"local/adapters/{harness}/run or core/adapters/{harness}/run")
    env = config.child_env()
    # The agent settings themselves never travel: the adapter is told the
    # model as AGENT_MODEL and nothing else, so a startup value of
    # BOARD_AGENT_MODEL_* folded into the child environment cannot
    # contradict what this launch resolved.
    for key in config.AGENT_KEYS:
        env.pop(key, None)
    env.update({
        "AGENT_PROMPT": prompt,
        "AGENT_MODE": mode,
        "AGENT_COMMANDS": config.AGENT_COMMANDS,
        "AGENT_CWD": str(cwd),
        # The hosting provider's verbs for this intent (task 116): the
        # adapter renders them beside its own git prefixes, so a GitHub
        # board's permission document is byte for byte what it was, and a
        # `none` board's carries no host verb at all.
        "AGENT_HOST": json.dumps(githost.agent_verbs(mode)),
        "BOARD_AGENT_ID": agent_id,
        "BOARD_TASK": filename,
        "BOARD_PORT": str(state.serve_port),
    })
    if manifest is not None:
        env["AGENT_PROFILE"] = json.dumps(profiles.adapter_policy(manifest))
        env["AGENT_CONTEXT"] = json.dumps(profiles.adapter_context(manifest))
    else:
        env.pop("AGENT_PROFILE", None)
        env.pop("AGENT_CONTEXT", None)
    # The workflow briefs the prompt names outside the checkout, by
    # canonical path and approved hash, so an adapter that grants reads
    # narrowly can grant exactly these (task 126). Absent when there are
    # none — every vendored board — so the adapter's environment is then
    # exactly what it always was.
    guidance = profiles.adapter_guidance(manifest)
    if guidance:
        env["AGENT_GUIDANCE"] = json.dumps(guidance)
    else:
        env.pop("AGENT_GUIDANCE", None)
    # The progress channel: one executable and a token scoped to this run,
    # handed over only when the adapter declares it can render the grant.
    # Absent, the adapter is given exactly the environment it always was.
    env.pop("AGENT_PROGRESS", None)
    env.pop("BOARD_PROGRESS_TOKEN", None)
    env.pop("BOARD_RUN_TOKEN", None)
    token = (progress.open_run(agent_id, filename, mode,
                               progress.channel(mode, (manifest or {}).get("adapter")))
             if mode in progress.INTENTS else None)
    if token:
        env["AGENT_PROGRESS"] = str(progress.shim())
        env["BOARD_PROGRESS_TOKEN"] = token
    # The run's own capability, issued whether or not it can report
    # progress: it is what proves a read of its own check diagnostics
    # (`bench-verify`), and nothing is read as a run without it.
    run_token = (progress.issue_capability(agent_id)
                 if mode in progress.INTENTS else None)
    if run_token:
        env["BOARD_RUN_TOKEN"] = run_token
    model = selection["model"]["value"]
    if model:
        env["AGENT_MODEL"] = model
    else:
        # Inherit = the variable is simply absent. Popping also stops a
        # stray AGENT_MODEL in the board's own environment leaking through.
        env.pop("AGENT_MODEL", None)
    log_file = log_path.open("wb")
    try:
        proc = subprocess.Popen(
            [str(adapter / "run")], cwd=str(cwd), env=env,
            stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except OSError as exc:
        log_file.close()
        progress.close_run(agent_id, "failed")
        raise ValueError(f"could not launch adapter {adapter}: {exc}")
    return proc, log_file, model


def _progress_fields(mode: str) -> dict[str, str]:
    """The `{progress}` placeholder: what the run is told about reporting,
    or nothing at all when its adapter gives it no channel — the same
    reading `launch` takes when it decides whether to hand one over."""
    available = progress.channel(mode).get("state") == "available"
    return {"progress": progress.prompt_block(mode, available)}


def _no_commands_note() -> str | None:
    """What a launch owes the ticker when the project configured nothing for
    its agents to run: this run can edit and commit, but it cannot check its
    own work. Only the two intents that would have run the commands say it
    (work and act-pr); a read-only kind never had them.

    A note, never a refusal — an agent that only edits files is still
    useful, and bench does not decline work because a project is
    unconfigured. The header says the same thing standing still."""
    if config.agent_commands():
        return None
    return ("no project commands configured, so it cannot run this project's "
            "tests — set BOARD_AGENT_COMMANDS in manager/local/.env")


def _profile_note(manifest: dict) -> str | None:
    """What a launch owes the ticker about the policy it rode.

    Silent on a legacy board, which is almost every board: there is
    nothing to say about settings nobody wrote. A board with a profile
    document gets the policy identity and how many inputs were pinned,
    because that is the fact a reader of the log will want and the
    manifest is otherwise machine-local.
    """
    if manifest["source"]["kind"] == "legacy":
        return None
    pinned = len([i for i in manifest["context"] if i["status"] == "pinned"])
    bits = [f"profile {manifest['policyId']}",
            f"{len(manifest['commands'])} named command grant"
            f"{'' if len(manifest['commands']) == 1 else 's'}"]
    if pinned:
        bits.append(f"{pinned} pinned input{'' if pinned == 1 else 's'}")
    return " · ".join(bits)


def _profiled(intent: str, filename: str, stage: str, *,
              worktree: Path | str | None = None,
              review: Path | str | None = None,
              review_record: dict | None = None,
              information: bool = False) -> dict:
    """Resolve, validate and pin the profile for one launch.

    `require` sits between the resolution and the pin, so no bundle is
    ever written for a launch that was refused — and every call site below
    makes this call *before* it creates a branch, a worktree or a process,
    so a refusal leaves nothing to explain. The manifest is filed under
    the run's id afterwards, once there is an id for it.
    """
    found = profiles.manifest(intent, filename, stage, worktree=worktree,
                              review=review, review_record=review_record)
    if information:
        # The card's own inputs (task 132), checked against this very
        # manifest — the run that will start — and added to what is pinned,
        # so they are copied, hashed and granted exactly as an approved
        # input is. Refuses before the pin when anything required is not
        # usable by this run.
        inputs.attach(found, filename, stage)
    found = profiles.require(found)
    profiles.pin(found)
    if information:
        inputs.confirm_delivery(found)
        # The card's Show the result request (task 133), frozen with the
        # run: a later edit reaches the next launch, never this one.
        found["demonstration"] = results.frozen(filename, stage)
    return found


def _context_block(manifest: dict) -> str:
    """The `{context}` placeholder: the operator's pinned inputs, then the
    card's own Inputs and required tools (task 132), then what the card
    asks to be shown (task 133) — one field, so a local prompt override
    written before cards had either still receives them."""
    return "\n".join(part for part in (profiles.prompt_block(manifest),
                                       inputs.prompt_block(manifest),
                                       results.prompt_block(
                                           manifest.get("demonstration")))
                     if part)


def _with_template(manifest: dict, template: dict) -> dict:
    """The manifest with the identity of the prompt template this run is
    filled from — name, source and hash, never the text — so the record
    says which prompt rode the launch beside which harness and model."""
    found = dict(manifest)
    found["template"] = templates.identity(template)
    return found


def preflight_profile(intent: str, filename: str, stage: str, *,
                       worktree: Path | str | None = None) -> None:
    """The same question asked with *nothing at all* written — not even a
    pinned bundle.

    One caller: the reading `start_agent` takes before it claims the card.
    The claim is a write, so what precedes it has to be free of side
    effects in a way even the pin is not.
    """
    profiles.require(profiles.manifest(intent, filename, stage,
                                       worktree=worktree))


def _claim_refusal(filename: str, holder: str | None, me: str | None,
                   takeover: bool) -> str | None:
    """Why the claim would refuse, or None — the one sentence both the
    claim and the read-only launch check give."""
    if holder and me and holder != me and not takeover:
        return (f"{holder} holds {filename} — take it over deliberately "
                f"(the card's ▸ take over), or clear the Assignee line")
    return None


def _work_preflight(filename: str, stage: str) -> list:
    """The refusals a work launch asks before its claim, in order, as
    steps: the stage and phase-card check, the plan, the profile and the
    prompt template. `start_agent` runs them and stops at the first; the
    launch check runs the same steps and writes nothing either way."""
    return [
        lambda: validate_launch(filename, stage, {"in-progress"},
                          "work starts from in-progress/ — move the card "
                          "there first", phase=PHASE_RUNS_WITH),
        lambda: plans.require(plans.preflight(filename, stage)),
        lambda: preflight_profile("work", filename, stage),
        # The card's required information (task 132): every required input
        # usable by the run that would start, every required tool granted.
        # Read, never fetched — a remote input needs a fresh check first.
        lambda: inputs.require_ready(filename, stage),
        # A local override that breaks its contract refuses here, before
        # the claim, the branch and the worktree (task 77).
        lambda: templates.require("work.md"),
    ]


def launch_refusals(filename: str, stage: str, intent: str = "work",
                    takeover: bool = False) -> list[str]:
    """Would this launch be refused right now, and why — asked of the
    same steps the launch itself takes before anything exists, with
    nothing claimed, branched, checked out, pinned or launched.

    `work` is ▸ start work's whole pre-claim path plus the claim's own
    ownership rule, read rather than written. Other intents get the
    refusals their launch shares with it: the stage and phase rules, one
    agent per card, the profile and the template. A refusal in the stage
    check ends the reading, because every later step is about a card in
    a stage it is not in."""
    found: list[str] = []
    if intent == "work":
        steps = _work_preflight(filename, stage)
    else:
        rules = {
            "act-pr": ({"review"}, "acting on a PR happens from review/",
                       None, "act-pr.md"),
            "review": (set(config.STAGE_DIRS), None, None, "review.md"),
        }
        if intent not in rules:
            return [f"there is no launch called {intent!r}"]
        allowed, why, phase, template = rules[intent]
        steps = [
            lambda: validate_launch(filename, stage, allowed, why, phase=phase),
            lambda: preflight_profile(intent, filename, stage),
            lambda: templates.require(template),
        ]
        if intent == "act-pr":
            steps.append(lambda: inputs.require_ready(filename, stage,
                                                      intent="act-pr"))
            # the repair target — a phase's integration branch, worktree
            # and PR, or the card's own task branch — read exactly as the
            # launch reads it (task 174), so the check and the launch
            # cannot disagree about what would be repaired
            steps.append(lambda: _require_target(filename, stage))
    for index, step in enumerate(steps):
        try:
            step()
        except (ValueError, OSError) as exc:
            found.append(str(exc))
            if index == 0:
                return found
    if config.COMMIT_MOVES:
        try:
            holder = read_task(config.TASKS / stage / filename,
                               stage).get("assignee")
        except OSError:
            holder = None
        refusal = _claim_refusal(filename, holder, actor_name(), takeover)
        if refusal:
            found.append(refusal)
    return found


def claim_for_launch(filename: str, stage: str, takeover: bool = False) -> None:
    """One agent per task is a board-memory rule; across machines the card
    file is the only thing every board can see, so the claim is what gates
    a launch here.

    Someone else's card refuses — naming who holds it — unless this is the
    deliberate takeover, which reassigns the card to whoever asked. An
    unclaimed card claims itself on launch: starting work is as much a
    commitment as the move that usually writes the line.

    Only in team mode. With `BOARD_COMMIT_MOVES` off nothing writes the
    assignee, so nothing may refuse on it either — the launch is exactly
    what it was before.
    """
    if not config.COMMIT_MOVES:
        return
    path = config.TASKS / stage / filename
    holder = read_task(path, stage).get("assignee")
    me = actor_name()
    refusal = _claim_refusal(filename, holder, me, takeover)
    if refusal:
        raise ValueError(refusal)
    if not me:
        return                       # no identity to write; git has no name here
    if holder == me:
        return
    set_assignee(filename, stage, me)
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": (f"{me} took {filename} over from {holder}" if holder
                    else f"{me} claimed {filename} by starting work on it")})


def start_agent(filename: str, stage: str, takeover: bool = False) -> dict:
    # Moving a card to in-progress is the commitment; only then does work
    # start — and a phase card is refused here, in the same breath and ahead
    # of the claim, so the refusal costs nothing and leaves nothing behind.
    # Preflight, before the claim: a plan that does not resolve must leave
    # the card exactly as it was — unclaimed, no branch, no worktree, no
    # run. Then once more after the claim, because the claim is a write and
    # the committed start action revalidates what it is about to act on.
    #
    # The profile is validated in the same breath and for the same reason:
    # a required input that is missing, a path that escapes its root, a
    # capability the installed adapter cannot enforce — each of those must
    # refuse *before* a billable model is invoked, and each of them leaves
    # the card untouched because neither reading writes anything. The same
    # steps are what `launch_refusals` asks, so the launch check and the
    # launch cannot disagree about them.
    for step in _work_preflight(filename, stage):
        step()
    card_path = config.TASKS / stage / filename
    unclaimed = card_path.read_text(encoding="utf-8")
    claim_for_launch(filename, stage, takeover)
    stem = filename[:-3]
    try:
        plan = plans.require(plans.preflight(filename, stage))
        # And the profile, pinned here rather than after the worktree: the
        # pin is the moment the run's inputs stop being able to change, and
        # the one thing worse than refusing late is refusing late with a
        # checkout already on disk. A bundle is content-addressed and
        # read-only, so one written for a launch that then failed on git is
        # reused by the next rather than left to be cleaned up.
        manifest = _profiled("work", filename, stage,
                             worktree=config.WORKTREES / stem,
                             information=True)
        template = templates.require("work.md")
    except ValueError:
        # The second reading refused — a fetch that failed between the two,
        # a branch deleted in the gap, a required input that vanished. A
        # launch that did not happen claims nothing, so the card goes back
        # to what it said before the claim, committed the same way the
        # claim was.
        if card_path.read_text(encoding="utf-8") != unclaimed:
            records.write_text(card_path, unclaimed, mkdir=False)
            commit_edit(filename, stage, "claim released: launch refused")
        raise

    branch = plan["branch"]
    worktree = Path(plan["worktree"])

    # Earlier work, a foreign checkout sitting where the worktree goes, a
    # branch that is not built on what the card now names: all of it was
    # answered by the preflight above, which refused before the claim
    # rather than here — including the repository-identity question every
    # other consumer of a worktree asks (`config.worktree_mismatch`).
    continuing = bool(plan["continuing"])
    base = plan["startCommit"]
    if not worktree.exists():
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        if continuing:                     # the branch is there, its worktree is not
            result = gitrepo.worktree_add(worktree, branch)
        else:
            result = gitrepo.worktree_add(worktree, branch, new=True,
                                          start=plan["startRef"] or None)
        if result.returncode != 0:
            raise ValueError(f"git worktree add failed: {result.stderr.strip()[:300]}")

    task = read_task(config.TASKS / "in-progress" / filename, "in-progress")

    agent_id = f"{stem}-{time.strftime('%H%M%S')}"
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Recorded before the run begins, so every later step — resuming, the
    # PR this card's move into review/ opens, acting on its reviews, the
    # merge at the end — reads one target rather than resolving its own.
    plan = plans.record(plan)
    # The manifest the same way, now that the run has an id to file it
    # under: from here what this run rode is a fact on disk, and a later
    # edit to the document or to an approved file changes the next launch
    # and never this one.
    manifest = profiles.record(agent_id, _with_template(manifest, template))

    prompt = templates.render("work.md", template["text"], dict(
        branch=branch, filename=filename, body=task["body"],
        plan=plans.brief(plan), context=_context_block(manifest),
        verification=profiles.verification_block(
            manifest, gatepolicy.required_lines(manifest)),
        **config.prompt_fields(filename, "in-progress"), **_progress_fields("work")))
    proc, log_file, model = launch("work", prompt, worktree, agent_id,
                                    filename, log_path, manifest)

    name = pick_name(stem)
    record = {
        "id": agent_id, "task": filename, "branch": branch,
        "worktree": str(worktree), "base": base, "status": "running",
        "rc": None, "started": time.time(), "session": None,
        "log": str(log_path), "proc": proc, "origin": stage, "mode": "work",
        "name": name, "model": model or None, "plan": plans.portable(plan),
        "profile": profiles.summary(manifest),
    }
    with state.LOCK:
        state.AGENTS[agent_id] = record
    summary = (f"{name} is back on {filename} — continuing branch {branch}"
               if continuing else
               f"{name} started on {filename} (branch {branch})")
    summary += f" — {plans.line(plan)}"
    for note in (*plan["notes"], _profile_note(manifest), _no_commands_note()):
        if note:
            summary += f" — {note}"
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": summary,
    })
    _start_reaper(_reap_agent, agent_id, proc, log_file)
    return _agent_public(record)


def start_review(filename: str, stage: str) -> dict:
    """Fire a read-only agent that checks the task against the codebase.

    Every stage, and a phase card too: no `phase=` here is the deliberate
    answer, not an omission. Nothing is written but the report.
    """
    validate_launch(filename, stage, config.STAGE_DIRS)
    # The read-only intent gets a resolved profile of its own now, rather
    # than the hardcoded grant set each adapter used to hold alone — so a
    # relevance check refuses a missing required input exactly as a work
    # launch does, before the model. Nothing exists to clean up at this
    # point, which is where every launch refusal belongs.
    manifest = _profiled("review", filename, stage)
    template = templates.require("review.md")

    task = read_task(config.TASKS / stage / filename, stage)
    # The brief this run is about to be given, by revision (task 168): the
    # exact text the prompt carries, so the filed review names what it
    # assessed — not the card as it reads when the report arrives.
    brief_revision = taskfiles.brief_sha(taskfiles.card_parts(task["body"])["brief"])
    agent_id = f"review-{filename[:-3]}-{time.strftime('%H%M%S')}"
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = profiles.record(agent_id, _with_template(manifest, template))

    prompt = templates.render("review.md", template["text"], dict(
        stage=stage, filename=filename, body=task["body"],
        context=profiles.prompt_block(manifest),
        verification=profiles.verification_block(
            manifest, gatepolicy.required_lines(manifest)),
        **config.prompt_fields(filename, stage), **_progress_fields("review")))
    proc, log_file, model = launch("review", prompt, config.REPO, agent_id,
                                    filename, log_path, manifest)

    name = pick_name(filename)
    record = {
        "id": agent_id, "task": filename, "branch": None, "worktree": None,
        "base": None, "status": "running", "rc": None, "started": time.time(),
        "session": None, "log": str(log_path), "proc": proc,
        "origin": stage, "mode": "review", "name": name, "model": model or None,
        "profile": profiles.summary(manifest), "briefRevision": brief_revision,
    }
    with state.LOCK:
        state.AGENTS[agent_id] = record
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": f"{name} is checking {filename} is still true of the codebase",
    })
    _start_reaper(_reap_review, agent_id, proc, log_file)
    return _agent_public(record)


def _gate_note(filename: str) -> str:
    """What the card's own gate says now that the run has ended, as one
    clause for the line that records it.

    The distinction it draws is the one an exit code cannot: a run that
    committed code and never asked bench for the project's checks has
    produced work *awaiting verification*, and a board that says
    "finished" about it is the green badge this whole design exists to
    prevent. Empty on a board that gates nothing, which is every board
    that declared no gate.
    """
    try:
        task = next((t for stage in collect()["stages"]
                     for t in stage["tasks"] if t["file"] == filename), None)
        scope = "member" if (task or {}).get("phase") else "acceptance"
        decision = gates.evaluate(filename, scope, task=task)
    except (ValueError, OSError):
        return ""
    if not decision["gated"]:
        return ""
    if decision["decision"] == "allowed":
        return f" — verified: {decision['summary']}"
    if decision["decision"] == "pending":
        return f" — awaiting verification: {decision['summary']}"
    return (f" — code is committed and NOT verified: {decision['summary']}")


def _declined_reason(log_path: str) -> str | None:
    """First line of a `NOT READY:` marker in the agent's final output."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for match in re.finditer(r"^NOT READY:\s*(.*)$", text, re.MULTILINE):
        reason = match.group(1).strip()
        if reason.startswith("<"):
            continue  # the prompt's own template line, echoed into the log
        return reason or "open questions"
    return None


def _no_new_commits(record: dict) -> bool:
    """True iff the worktree's HEAD is still the commit the agent started
    from — i.e. the run produced no commits on the branch."""
    if not record.get("worktree") or not record.get("base"):
        return False
    head = gitrepo.git(record["worktree"], "rev-parse", "HEAD", timeout=None)
    return head.returncode == 0 and head.stdout.strip() == record["base"]


def _discard_untouched_worktree(record: dict) -> tuple[bool, str]:
    """Remove worktree + branch, but only when there is provably nothing
    in them. Returns (removed, why it was kept).

    An unchanged HEAD is *not* the same as a clean checkout. A run that
    edited files and never committed — a permission failure, an API error
    mid-edit, a decline after some exploration — leaves exactly that: no
    commits, and work on disk. Forcing the worktree out threw it away,
    and it is the one unrecoverable thing in this whole module. So
    uncommitted changes and untracked files both keep it, the removal
    runs without `--force`, and a git that refuses anyway is reported
    rather than assumed to have succeeded.

    And the recorded plan is forgotten only when the branch it described
    really went: a worktree kept because there is work in it keeps its
    plan too, because the work is still on the branch the plan names.
    """
    if not _no_new_commits(record):
        return False, "it has commits"
    worktree = record["worktree"]
    dirty = gitrepo.dirty(worktree)
    if dirty:
        return False, (f"{len(dirty)} uncommitted change"
                       f"{'' if len(dirty) == 1 else 's'} in it")
    removed = gitrepo.worktree_remove(worktree)
    if removed.returncode != 0:
        detail = (removed.stderr.strip() or removed.stdout.strip()
                  or "git would not remove it")
        return False, detail.splitlines()[-1][:120]
    gitrepo.branch_delete(record["branch"])
    # The branch it described is gone, so the plan would describe nothing.
    plans.forget(record["task"])
    return True, ""


def _failure_excerpt(log_path: str | None, lines: int = 6, cap: int = 600) -> str:
    """The tail of a dead run's log, cleaned — usually the whole story
    ("API Error: 500 …"). A launch that died before the agent ever spoke
    leaves a line or two, or nothing at all; say which rather than showing
    an empty card."""
    text = ""
    if log_path:
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    kept = [line.rstrip() for line in reports.tail(text, 8000).splitlines()
            if line.strip()]
    if not kept:
        return "no output — the run died before the agent said anything"
    return "\n".join(kept[-lines:])[-cap:]


def _headline(excerpt: str, cap: int = 120) -> str:
    """One line of an excerpt for a ticker line or a toast: the last one,
    which is where a dying process says why."""
    lines = [line for line in (excerpt or "").splitlines() if line.strip()]
    return (lines[-1].strip()[:cap] if lines else "no output")


def died_on(record: dict) -> str:
    """What a dead run's log ended on, for the ticker line that records it."""
    return _headline((record.get("failure") or {}).get("excerpt", ""))


def _record_failure(record: dict, rc: int) -> dict:
    """A dead run is a state its card wears, not an event that scrolls by.

    Every headless kind lands here, launches that died before the agent
    spoke included: the outcome goes onto the run's record — exit code,
    when it ended, the log's cleaned tail, and the stage the card was in —
    so the board can show it, and a toast says it once to whoever is
    looking. The stage is part of the state because the state is about
    work in that stage: carried into review/ it would libel the next run.
    """
    failure = {
        "rc": rc,
        "ended": record.get("ended") or time.time(),
        "excerpt": _failure_excerpt(record.get("log")),
        "stage": find_stage_of(record["task"]) or record.get("origin"),
        "log": record.get("log"),
        "mode": record.get("mode", "work"),
    }
    with state.LOCK:
        record["failure"] = failure
    name = record.get("name") or "the agent"
    state.broadcast({
        "type": "toast", "error": True,
        "message": f"{name} failed on {record['task']} (rc={rc}) — "
                   f"{_headline(failure['excerpt'])}",
    })
    return failure


def forget_failure(filename: str) -> bool:
    """Drop a card's failed-run state. Called when the card moves stage:
    the failure belonged to the work in the stage it died in, and no card
    should arrive somewhere new already wearing an alarm. A relaunch needs
    no call — the newer run is what the card reads."""
    cleared = False
    with state.LOCK:
        for record in state.AGENTS.values():
            if record["task"] == filename and record.pop("failure", None):
                cleared = True
    return cleared


def finish(agent_id: str, proc: subprocess.Popen, log_file) -> tuple[dict, bool, int]:
    rc = proc.wait()
    log_file.close()
    # A run that has gone cannot be waiting on a check, and evidence for a
    # run that ended mid-verification must say so rather than arriving
    # afterwards as though the run had seen it.
    verify.cancel_for_agent(agent_id)
    with state.LOCK:
        record = state.AGENTS[agent_id]
        stopped = record["status"] == "stopped"
        record["status"] = "stopped" if stopped else ("done" if rc == 0 else "failed")
        record["rc"] = rc
        record["ended"] = time.time()
        failed = record["status"] == "failed"
    # The run's own account of its progress stops being "doing now" the
    # moment the board sees it end; what it said stays, as dated history.
    progress.close_run(agent_id, "held" if stopped else
                       ("finished" if rc == 0 else "failed"))
    if failed:
        _record_failure(record, rc)
    return record, stopped, rc


def _reap_agent(agent_id: str, proc: subprocess.Popen, log_file) -> None:
    record, stopped, rc = finish(agent_id, proc, log_file)
    filename, branch = record["task"], record["branch"]
    name = record.get("name") or "the agent"

    declined = None if (stopped or rc != 0) else _declined_reason(record["log"])
    if declined is not None:
        with state.LOCK:
            record["status"] = "declined"
        progress.close_run(agent_id, "declined")
        # Send the card back for refinement and clear the way for a relaunch.
        back_to = record["origin"] if record["origin"] in ("backlog", "to-do") else "to-do"
        if find_stage_of(filename) == "in-progress":
            try:
                move_task(filename, "in-progress", back_to, actor="agent")
            except ValueError:
                pass
        cleaned, kept = _discard_untouched_worktree(record)
        summary = (f"{name} declined {filename} — not ready: {declined}"
                   + ("" if cleaned else
                      f" (worktree {record['worktree']} kept: {kept})"))
    elif rc == 0 and not stopped:
        report = _report_of(record)
        _file_report(record, "Work report", report)
        _session_report(record, report)
        if _no_new_commits(record):
            # A "clean" exit with an empty branch is how permission bugs
            # hide: nothing reaches review/ silently.
            summary = (f"{name} exited cleanly on {filename} but committed "
                       f"NOTHING to {branch} — card stays in in-progress; "
                       f"read the report before relaunching")
        else:
            # An agreed "Show the result: required for handoff" whose
            # output is missing for this commit holds the handoff (task
            # 133): the card stays in in-progress and the ticker names the
            # remedy. Requested and Off never hold anything.
            held = (results.handoff_refusal(filename, "in-progress", "review")
                    if find_stage_of(filename) == "in-progress" else None)
            if held is None and find_stage_of(filename) == "in-progress":
                try:
                    move_task(filename, "in-progress", "review", actor="agent")
                except ValueError:
                    pass
            # Code produced is not work verified, and on a board that
            # gates one it is the difference between a card that can
            # advance and a card that cannot. So the ending says which it
            # is — read from the gate rather than from the exit code that
            # got us here, and silent on a board that gates nothing.
            summary = (f"{name} finished {filename} — review branch {branch}"
                       f"{_gate_note(filename)}")
            if held:
                summary = (f"{name} finished {filename} on {branch}; the card "
                           f"stays in in-progress — {held}")
    elif stopped:
        summary = f"{name} was held on {filename} — nothing is lost"
    else:
        # The card now wears the failure; the ticker keeps the record of it
        # and names what the log's tail said. A run that committed nothing
        # also leaves nothing worth keeping, so the worktree goes and
        # ▸ start work is one click again — same reasoning as a decline.
        cleaned, kept = _discard_untouched_worktree(record)
        summary = (f"{name} exited on {filename} rc={rc} — {died_on(record)}"
                   + (" (worktree cleared — relaunch when you have read it)" if cleaned
                      else f" (worktree {record['worktree']} kept: {kept})"))
    state.record_board_event({"kind": "agent", "actor": "agent", "file": filename,
                              "summary": summary})
    state.broadcast({"type": "agents"})


def start_pr_review(filename: str, stage: str) -> dict:
    """Fire a read-only agent that reviews the task's PR and posts the
    verdict to GitHub as well as back to the board.

    A phase card is allowed here, deliberately: the PR into `main` is what
    the whole run exists to produce, and reading it writes nothing.

    The review does **not** run in the application checkout. `reviews`
    resolves the pull request's base and head, and bench adds a detached
    checkout of that exact head for this run to work in — so the review
    reads the code the PR proposes, at a revision that cannot change under
    it, in a directory no other checkout shares. Everything is resolved
    before anything is created, in this order:

    1. the identity, which reads and fetches and creates nothing, so a PR
       whose head bench cannot name refuses with the card untouched;
    2. the profile, resolved and pinned against the path the checkout
       *will* be at — derivable from the head, so no worktree exists yet
       for a refusal to leave behind;
    3. the checkout itself, recorded as owned by this run;
    4. the manifest, which carries the review record, so `verify` and the
       page read what this run was pinned rather than asking again.
    """
    validate_launch(filename, stage, {"review"},
              "PR reviews run on cards in review/")
    task = read_task(config.TASKS / stage / filename, stage)
    if not task.get("pr"):
        raise ValueError(f"{filename} has no PR yet — nothing to review")

    found = reviews.identity(filename, task)
    if found["problem"]:
        raise ValueError(found["problem"])
    stem = filename[:-3]
    checkout = reviews.checkout_path(stem, found["head"]["commit"])
    # Resolved and pinned here rather than after the checkout, for the
    # reason the work launch pins before its worktree: the one thing worse
    # than refusing late is refusing late with a checkout already on disk.
    # The path is derivable from the head, so this needs nothing to exist
    # yet — and a bundle is content-addressed and read-only, so one
    # written for a launch that then failed is reused by the next.
    planned = _profiled("review", filename, stage, review=checkout)
    template = templates.require("review-pr.md")

    # …and it must be told the branch its PR is actually from: the prompt
    # asks GitHub for the diff by branch, and a phase's is its own.
    branch = phase_branch(filename) if task["isPhase"] else f"task/{stem}"
    name = pick_name(filename)
    agent_id = f"review-pr-{stem}-{time.strftime('%H%M%S')}"
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    pinned = reviews.provision(filename, stage, agent_id, found)
    try:
        resolved = profiles.require(profiles.manifest(
            "review", filename, stage, review=checkout,
            review_record=pinned, pin_inputs=True))
        # The harness and model the preflight validated are the ones this
        # launch rides; a settings change landing in between refuses
        # rather than launching something nobody preflighted.
        if resolved.get("selection") != planned.get("selection"):
            raise ValueError("the agent settings changed while this review was "
                             "being prepared — nothing was launched; ask again")
        manifest = profiles.record(agent_id, _with_template(resolved, template))

        prompt = templates.render("review-pr.md", template["text"], dict(
            filename=filename, pr=task["pr"], branch=branch, body=task["body"],
            review=reviews.prompt_block(pinned),
            context=profiles.prompt_block(manifest),
            verification=profiles.verification_block(
                manifest, gatepolicy.required_lines(manifest)),
            **githost.fragments("review-pr.md", branch),
            **config.prompt_fields(filename, stage), **_progress_fields("review")))
        proc, log_file, model = launch("review", prompt, Path(pinned["path"]),
                                        agent_id, filename, log_path, manifest)
    except BaseException:
        # Nothing may be left holding a checkout for a run that never
        # started — the record is the only thing that could say who owns
        # it, and there would be no run to ask.
        reviews.release(agent_id, "the review was never launched")
        raise

    record = {
        "id": agent_id, "task": filename, "branch": branch,
        # Not `worktree`: that key is a card's own working checkout, which
        # `agent_diff`, the drive and the local commands all read as
        # editable. This one is read-only and bench's.
        "worktree": None,
        "base": found["head"]["commit"], "status": "running", "rc": None,
        "started": time.time(), "session": None, "log": str(log_path),
        "proc": proc, "origin": stage, "mode": "review", "name": name,
        "model": model or None, "profile": profiles.summary(manifest),
        "review": reviews.summary(pinned),
    }
    with state.LOCK:
        state.AGENTS[agent_id] = record
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": (f"{name} is reviewing {filename}'s PR — "
                    f"{reviews.line(pinned)}"),
    })
    _start_reaper(_reap_pr_review, agent_id, proc, log_file)
    return _agent_public(record)


# ── what ↻ act on PR repairs (task 174) ─────────────────────────────────
#
# One resolver for every door into a PR repair — the person's click, the
# launch check and a delegated repair an orchestrator scope selects — so
# manual and automated launches ride the same validated target contract.
#
# An ordinary card's target is what it always was: `task/<stem>`, its
# worktree and its PR, refused only when the PR is merged or closed.
#
# A phase card's target is its *integration* work: the existing
# `phase/<stem>` branch the runner (or its phase scope) merged every member
# into, the Bench-managed worktree beside it and the open integration PR.
# Never a task branch, a member, a new card or a replacement PR. Because a
# push there changes what the phase delivers, it is held to more before
# anything is created: the PR must be open and still propose exactly that
# branch into the base the board recorded; the local branch must exist and
# contain the head the PR proposes; the worktree must be this repository's,
# on that branch, clean and not mid-merge — or absent, and then it is
# recreated from the branch; and nothing may be working on the phase or any
# member, or be held by an unsettled operation. Each refusal names its
# prerequisite and changes nothing: no reset, no force-push, no branch
# switch, no edit of another run's checkout.

REPAIR_TARGET_VERSION = 1

# who is preparing a PR repair right now: a second click lands here
# before the registry has a record, and is refused rather than doubled
_REPAIRING: set[str] = set()


class RepairRefused(ValueError):
    """A repair target that cannot be acted on, with the code a delegated
    repair's receipt records (`coordinate` reads `code`)."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code


def _phase_members(card: dict) -> list[str]:
    return [m["file"] for m in card.get("members") or [] if m.get("file")]


def _worktree_state(path: Path, branch: str) -> tuple[str | None, str]:
    """(code, sentence) when an existing worktree cannot be repaired in;
    (None, "") when it can. Reads only."""
    mismatch = gitrepo.worktree_mismatch(path)
    if mismatch:
        return "foreign-checkout", mismatch
    current = gitrepo.git(path, "branch", "--show-current").stdout.strip()
    if current != branch:
        return ("wrong-branch",
                f"the worktree at {path} is on '{current or 'a detached HEAD'}', "
                f"not {branch} — bench will not switch it; settle it by hand")
    gitdir = gitrepo.git(path, "rev-parse", "--absolute-git-dir").stdout.strip()
    if gitdir and any((Path(gitdir) / name).exists() for name in
                      ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD",
                       "rebase-merge", "rebase-apply")):
        return ("conflicting",
                f"the worktree at {path} is in the middle of a merge or rebase "
                f"— finish or abort it by hand first; nothing was changed")
    dirty = gitrepo.git(path, "status", "--porcelain").stdout.strip()
    if dirty:
        count = len(dirty.splitlines())
        return ("dirty",
                f"the worktree at {path} has {count} uncommitted "
                f"change{'' if count == 1 else 's'} — commit or remove "
                f"{'it' if count == 1 else 'them'} by hand first; bench "
                f"does not repair on top of work it cannot account for")
    return None, ""


def repair_target(filename: str, stage: str, *,
                  operation: str | None = None) -> dict:
    """What ↻ act on PR would repair for this card, and whether it may.

    A read: it creates no branch, worktree, claim or run (an ordinary
    card's answer may ask the hosting provider for its PR's state, as it
    always did). `problem` empty means the launch may go ahead; `code`
    names the refusal. `operation` is the delegated repair's own ledger
    entry, which is not a competing one."""
    stem = filename[:-3]
    card = plans.woven(filename, stage)
    phase = bool(card.get("isPhase"))
    branch = (config.PHASE_BRANCH_PREFIX if phase
              else config.TASK_BRANCH_PREFIX) + stem
    worktree = config.WORKTREES / stem
    out: dict = {
        "v": REPAIR_TARGET_VERSION, "kind": "phase" if phase else "task",
        "card": filename, "stage": stage, "branch": branch,
        "worktree": str(worktree), "pr": card.get("pr") or None,
        "base": None, "head": None, "state": None,
        "recreate": not worktree.exists(),
        "members": _phase_members(card) if phase else [],
        "notes": [], "problem": None, "code": None,
    }

    def refuse(code: str, sentence: str) -> dict:
        out.update(code=code, problem=sentence)
        return out

    if stage != "review":
        return refuse("stage", "acting on a PR happens from review/")
    if not card.get("pr"):
        if phase:
            return refuse("no-pr", f"{filename} has no integration PR to act "
                          f"on — ↑ open PR on the phase card first")
        return refuse("no-pr", f"{filename} has no PR to act on")
    # A merged or closed pull request is not a target a push repairs, and
    # a phase member's PR is merged into the phase branch early — so this
    # is the common case rather than the rare one. Refused here, ahead of
    # the profile, the worktree and the model, so nothing is pushed and
    # nothing is represented as having repaired the integration branch.
    found = reviews.identity(filename, card, fetch=False)
    out["state"] = found.get("state")
    out["head"] = dict(found.get("head") or {})
    out["base"] = plans.pr_base(filename, card)
    obsolete = reviews.repair_problem(filename, card, found=found)
    if obsolete:
        return refuse("pr-" + str(found.get("state") or "ended").lower(),
                      obsolete)
    if not phase:
        return out

    # ── the integration target, held to more ──
    proposed = (found.get("head") or {})
    if proposed.get("authority") in (None, "local"):
        # The host could not be read: the phase's own branch in this
        # checkout is the target, as it is for an ordinary card. The
        # PR's head and base cannot be compared here, and that is said
        # rather than assumed; what keeps the repair safe is that its push
        # is a plain one, which the remote refuses unless it fast-forwards
        # what the PR already proposes.
        out["notes"].append("the hosting provider could not be read, so the "
                            "integration PR's head and base were not checked "
                            "against this branch; the repair's push is a plain "
                            "one, which the remote refuses unless it "
                            "fast-forwards the PR's branch")
    if proposed.get("authority") not in (None, "local") \
            and proposed.get("ref") and proposed["ref"] != branch:
        return refuse("head-drift",
                      f"the integration PR ({card['pr']}) proposes "
                      f"{proposed['ref']}, not {branch} — bench repairs the "
                      f"phase's own branch only; fix the PR or the card's "
                      f"**PR:** line by hand")
    # a base read from the plan is the plan's own answer, so any other
    # value is the host's — and it must be the base the board recorded
    said_base = (found.get("base") or {})
    if said_base.get("authority") != "plan" \
            and said_base.get("ref") and said_base["ref"] != out["base"]:
        return refuse("base-drift",
                      f"the integration PR targets {said_base['ref']}, and the "
                      f"board recorded {out['base']} for this phase — settle "
                      f"which is right (⌖ replan, or change the PR back) before "
                      f"repairing it")
    if not gitrepo.branch_exists(branch):
        return refuse("no-branch",
                      f"{branch} is not in this checkout, so there is nothing "
                      f"to repair in — fetch it (Check the remote) first")
    tip = gitrepo.git(config.REPO, "rev-parse", "--verify", "--quiet",
                      f"refs/heads/{branch}").stdout.strip()
    commit = proposed.get("commit")
    if commit and proposed.get("authority") != "local" and commit != tip:
        held = gitrepo.git(config.REPO, "merge-base", "--is-ancestor",
                           commit, tip).returncode == 0
        if not held:
            return refuse("head-drift",
                          f"the integration PR's head {commit[:12]} is not in "
                          f"this checkout's {branch} ({tip[:12]}) — someone "
                          f"pushed to it; bring it in here (fetch and "
                          f"fast-forward) before repairing, so the repair "
                          f"builds on what the PR proposes")
        out["notes"].append(f"{branch} holds commits the PR does not yet "
                            f"show; the repair's push publishes them too")
    out["head"]["local"] = tip
    if worktree.exists():
        code, sentence = _worktree_state(worktree, branch)
        if code:
            return refuse(code, sentence)
    else:
        mismatch = gitrepo.worktree_mismatch(worktree)
        if mismatch:
            return refuse("foreign-checkout", mismatch)
        out["notes"].append(f"the phase's worktree is not on disk; it will be "
                            f"recreated from {branch}")
    # nothing else may be working on the phase or any member: one agent at
    # a time on the integration branch, and a member's run would merge into
    # the same branch
    code, sentence = phase_repair_wait(filename, out["members"],
                                       operation=operation)
    if code:
        return refuse(code, sentence)
    return out


def phase_repair_wait(filename: str, members: list[str], *,
                      operation: str | None = None) -> tuple[str | None, str]:
    """(code, sentence) when something else is working on a phase or any
    of its members, so a repair of its integration branch must wait;
    (None, "") when nothing is. A read of the run registry and the
    operation ledger: the one answer the launch and the orchestrator's
    plan both ask (task 174), so the plan waits rather than opening an
    operation the launch would refuse on every pass."""
    cards = {filename, *members}
    busy = working_on(cards)
    if busy:
        who = busy[0]
        return ("busy", f"{who.get('name') or 'an agent'} is working on "
                f"{who['task']} — ‖ hold it, or let it end, before "
                f"repairing the phase's integration branch")
    for op in operations.all_ops():
        if op.get("file") not in cards \
                or op.get("key") == operation \
                or op.get("status") not in operations.RESERVING:
            continue
        if op["status"] == "unknown":
            return ("operation-unknown",
                    f"operation {op['key']} ({op.get('action')}) on "
                    f"{op['file']} has an unknown outcome — settle it "
                    f"in Orchestrator → Recovery before repairing")
        return ("operation-pending",
                f"operation {op['key']} ({op.get('action')}) on "
                f"{op['file']} is still {op['status']} — wait for its "
                f"receipt before repairing")
    return None, ""


def public_target(target: dict) -> dict:
    """What a reader is given: words, refs and commits, never a path."""
    keep = ("v", "kind", "card", "branch", "pr", "base", "state", "recreate",
            "members", "notes", "problem", "code")
    out = {k: target.get(k) for k in keep}
    head = target.get("head") or {}
    out["head"] = {k: head.get(k) for k in ("ref", "commit", "authority",
                                             "local")}
    return out


def _require_target(filename: str, stage: str) -> dict:
    target = repair_target(filename, stage)
    if target["problem"]:
        raise RepairRefused(target["code"], target["problem"])
    return target


def repair_brief(target: dict) -> str:
    """What a run repairing a phase's integration PR is told about its
    target, beside its plan — nothing for an ordinary card, whose brief
    is unchanged."""
    if target.get("kind") != "phase":
        return ""
    members = ", ".join(m[:-3] for m in target.get("members") or []) or "none"
    return (
        f"\n\nThis is a phase card's integration PR. Repair it on "
        f"`{target['branch']}` only — the branch every member was merged "
        f"into — and push to that same branch so the same PR updates. Its "
        f"members ({members}) are already merged into it: do not reopen, "
        f"recreate or push to a member's branch or PR, and do not open a new "
        f"pull request. The review feedback to address is the integration "
        f"review on this PR and the review sections in the card below.")


def integration_brief(found: dict | None) -> str:
    """What a run updating a card against its target is told (task 189):
    the accepted target commits it must bring in, the conflict or check
    findings, and that a clean textual merge is not proof the combination
    works. Nothing for an ordinary review repair."""
    if not found:
        return ""
    incoming = "\n".join(f"  - {c['commit'][:12]} {c.get('subject') or ''}"
                         for c in found.get("incoming") or []) or "  - (not listed)"
    conflicts = ", ".join(found.get("conflicts") or []) or "none recorded"
    findings = found.get("findings") or "none recorded"
    return (
        f"\n\nThis run updates the card's own branch against its target "
        f"`{found['target']}` at `{(found.get('targetTip') or '')[:12]}` — "
        f"accepted work only. Bring that commit in with an additive merge "
        f"(never rebase, never force-push, and merge no other branch or "
        f"pull request). The accepted target commits this branch does not "
        f"hold yet, newest first:\n{incoming}\nFiles git reported "
        f"conflicting: {conflicts}. Check findings to address: {findings}. "
        f"Even where git merges cleanly, read the incoming changes against "
        f"this card's own behaviour and keep both working: shared code, "
        f"tests and regressions either side relies on. Run the checks you "
        f"are offered on the result, commit, and push so the same pull "
        f"request updates. The combined head needs its own checks and a "
        f"fresh review before it can be admitted; this run is neither.")


def start_pr_fix(filename: str, stage: str, *,
                 operation: str | None = None,
                 integration: dict | None = None) -> dict:
    """Fire a work agent that addresses the review feedback on the card's
    PR, working in its existing worktree (recreated from the branch if it
    was cleaned up), committing and pushing to update the PR.

    The target is `repair_target`'s: an ordinary card's `task/<stem>`, or
    a phase card's existing integration branch, worktree and PR."""
    validate_launch(filename, stage, {"review"},
                    "acting on a PR happens from review/")
    with state.LOCK:
        if filename in _REPAIRING:
            raise RepairRefused("busy", f"a PR repair on {filename} is "
                                f"already being started — nothing was doubled")
        _REPAIRING.add(filename)
    try:
        return _start_pr_fix(filename, stage, operation, integration)
    finally:
        with state.LOCK:
            _REPAIRING.discard(filename)


def _start_pr_fix(filename: str, stage: str, operation: str | None,
                  integration: dict | None = None) -> dict:
    target = repair_target(filename, stage, operation=operation)
    if target["problem"]:
        raise RepairRefused(target["code"], target["problem"])
    task = read_task(config.TASKS / stage / filename, stage)

    stem = filename[:-3]
    branch = target["branch"]
    worktree = config.WORKTREES / stem
    # Resolved and pinned before the worktree is recreated, not after: a
    # profile that cannot be honoured must not leave a checkout behind to
    # explain, and the pin is the moment this run's inputs stop being able
    # to change under it.
    manifest = _profiled("act-pr", filename, stage, worktree=worktree,
                         information=True)
    template = templates.require("act-pr.md")
    # The same plan the work was launched on, revalidated before the run
    # that will push to the PR: a card whose requirements changed under it,
    # or whose base or target has disappeared, needs a person rather than a
    # run that resolves conflicts against a base nobody chose. Preflight is
    # the whole check — it resolves both branches by name and refuses when
    # either is gone — so there is no second reading here to disagree with.
    # Preflight reads the card woven, not `task` above: phase membership is
    # derived across the board, and a member read alone looks like an
    # ordinary card on the application default. It carries the
    # repository-identity refusal with it: a foreign checkout sitting where
    # this card's worktree goes is a blocker there, not a second check here.
    plan = plans.require(plans.preflight(filename, stage))
    if plan["branch"] != branch:
        # the plan and the repair target name one branch, or nothing runs
        raise RepairRefused("plan-drift", f"the plan resolves {plan['branch']} "
                            f"and the repair target is {branch} — nothing was "
                            f"started; ⌖ replan first")
    plan = plans.record(plan)
    made = gitrepo.ensure_worktree(stem, branch)
    if made["problem"] == "mismatch":
        raise ValueError(made["detail"])
    if made["problem"] == "no-branch":
        raise ValueError(f"branch {branch} does not exist locally — nothing to act in")
    if made["problem"] == "add-failed":
        raise ValueError(f"could not recreate the worktree: {made['detail'][:200]}")

    name = pick_name(stem)
    agent_id = f"fix-pr-{stem}-{time.strftime('%H%M%S')}"
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = profiles.record(agent_id, _with_template(manifest, template))

    prompt = templates.render("act-pr.md", template["text"], dict(
        filename=filename, branch=branch, pr=task["pr"], body=task["body"],
        base=plan["prBase"], remote=plan.get("appRemote") or "origin",
        plan=(plans.brief(plan) + repair_brief(target)
              + integration_brief(integration)),
        context=_context_block(manifest),
        verification=profiles.verification_block(
            manifest, gatepolicy.required_lines(manifest)),
        **githost.fragments("act-pr.md", branch),
        **config.prompt_fields(filename, stage), **_progress_fields("act-pr")))
    # act-pr is the one intent allowed to push: the PR must update.
    proc, log_file, model = launch("act-pr", prompt, worktree, agent_id,
                                    filename, log_path, manifest)

    record = {
        "id": agent_id, "task": filename, "branch": branch,
        "worktree": str(worktree), "base": None, "status": "running",
        "rc": None, "started": time.time(), "session": None,
        "log": str(log_path), "proc": proc, "origin": stage, "mode": "work",
        "name": name, "model": model or None, "plan": plans.portable(plan),
        "profile": profiles.summary(manifest),
        "repair": {"kind": target["kind"], "branch": branch,
                   "pr": target["pr"], "base": target["base"],
                   "head": (target.get("head") or {}).get("local")
                   or (target.get("head") or {}).get("commit"),
                   "operation": operation},
    }
    if integration:
        record["integration"] = {k: integration.get(k) for k in
                                 ("target", "targetTip", "head")}
    if target["kind"] == "phase":
        # the commit the repair starts from, so the run's diff is its own
        record["base"] = (target.get("head") or {}).get("local")
    with state.LOCK:
        state.AGENTS[agent_id] = record
    summary = (f"{name} is acting on the review of {filename}'s integration "
               f"PR on {branch}" if target["kind"] == "phase" else
               f"{name} is acting on the review of {filename}'s PR")
    for note in (_profile_note(manifest), _no_commands_note()):
        if note:
            summary += f" — {note}"
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": summary,
    })
    _start_reaper(_reap_pr_fix, agent_id, proc, log_file)
    return _agent_public(record)


def _reap_pr_fix(agent_id: str, proc: subprocess.Popen, log_file) -> None:
    record, stopped, rc = finish(agent_id, proc, log_file)
    filename = record["task"]
    name = record.get("name") or "the agent"

    if rc == 0 and not stopped:
        try:
            text = Path(record["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        idx = text.find("ADDRESSED:")
        report = _report_of(record, text[idx:] if idx >= 0 else text)
        _file_report(record, "PR update", report)
        _session_report(record, report)
        summary = f"{name} acted on {filename}'s PR — re-review when ready"
    elif stopped:
        summary = f"{name} was held while acting on {filename}'s PR"
    else:
        summary = f"{name} failed acting on {filename}'s PR (rc={rc}) — {died_on(record)}"
    state.record_board_event({"kind": "agent", "actor": "agent", "file": filename,
                              "summary": summary})
    state.broadcast({"type": "board"})
    state.broadcast({"type": "agents"})


def _reap_pr_review(agent_id: str, proc: subprocess.Popen, log_file) -> None:
    # The run's process ends before its verdict is recorded — a host call
    # resolving the head sits between the two. Until this reaper has
    # finished, the record says `verdictPending`, so a reading taken in
    # that window says the review is still in progress rather than that
    # it ended without a verdict. `finally`: a reaper that raises must not
    # leave the card reading "in progress" for ever.
    with state.LOCK:
        if agent_id in state.AGENTS:
            state.AGENTS[agent_id]["verdictPending"] = True
    try:
        _settle_pr_review(agent_id, proc, log_file)
    finally:
        with state.LOCK:
            if agent_id in state.AGENTS:
                state.AGENTS[agent_id].pop("verdictPending", None)
        state.broadcast({"type": "board"})


def _settle_pr_review(agent_id: str, proc: subprocess.Popen, log_file) -> None:
    record, stopped, rc = finish(agent_id, proc, log_file)
    filename = record["task"]
    name = record.get("name") or "the reviewer"

    # Before the verdict is written anywhere, ask the pull request what its
    # head is *now*. A push that landed during the review means the verdict
    # is about an earlier revision — which is a fact the record must carry,
    # because a review that reads as the current head's approval when it is
    # not is exactly the green badge this card exists to prevent. Nothing
    # is re-run and nothing is retargeted: the review said what it said,
    # about the commit it said it about.
    pinned = reviews.owned(agent_id) or {}
    stage = find_stage_of(filename)
    try:
        card = read_task(config.TASKS / stage / filename, stage) if stage else None
    except OSError:
        card = None
    reviews.resolve_head(filename, card)
    moved = reviews.freshness(pinned) if pinned else {"stale": False,
                                                      "changed": []}
    note = moved["changed"][0] if moved.get("changed") else ""

    verdict = None
    if rc == 0 and not stopped:
        try:
            text = Path(record["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        idx = text.find("PR REVIEW:")
        report = _report_of(record, text[idx:] if idx >= 0 else text)
        match = re.search(r"^PR REVIEW:\s*(APPROVE|REQUEST CHANGES)", report)
        verdict = match.group(1) if match else None
        _file_report(record, _review_heading(pinned, moved), report,
                     _pr_provenance(agent_id, pinned, moved, text))
        _session_report(record, report)
        if verdict:
            # Bench's own record of what its reviewer found, bound to the
            # head bench pinned it to — written whether or not GitHub
            # accepted a formal review of it. That separation is the
            # point: the account that opened a pull request cannot
            # approve it, so a board reviewing its own work would
            # otherwise have no verdict at all rather than a verdict
            # GitHub would not hold.
            try:
                gates.record_verdict(
                    filename, verdict=verdict,
                    head=(pinned.get("head") or {}).get("commit"),
                    agent=agent_id, actor="agent",
                    review_id=pinned.get("reviewId"),
                    note=moved["changed"][0] if moved.get("changed") else "",
                    published=_publication_note(text))
            except (ValueError, OSError):
                pass

    if stopped:
        summary = f"{name}'s PR review of {filename} was held"
    elif rc != 0:
        summary = f"{name}'s PR review of {filename} died (rc={rc}) — {died_on(record)}"
    elif verdict is None:
        summary = f"{name}'s PR review of {filename} ended without a verdict — see its log"
    else:
        word = "approved it" if verdict == "APPROVE" else "asked for changes"
        head = (pinned.get("head") or {}).get("commit") or ""
        summary = (f"{name} reviewed {filename}'s PR at "
                   f"{head[:12] or 'an unrecorded revision'} and {word}")
        if note:
            summary += f" — {note}"
    state.record_board_event({"kind": "agent", "actor": "agent", "file": filename,
                              "summary": summary})

    # The checkout goes back whatever the ending was — held, failed, done.
    # Ownership is the record's, so this removes only what bench made, only
    # when nothing uncommitted is in it, and reports rather than forces.
    given = reviews.release(agent_id, f"the review ended ({record['status']})")
    if given and given.get("kept"):
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"{given['kept']} — nothing uncommitted is thrown away"})
    state.broadcast({"type": "board"})
    state.broadcast({"type": "agents"})


# What GitHub says when the account that opened a pull request tries to
# review it formally. Matched in the *run's own log*, which is the only
# place the attempt is visible at all — and recorded as a note about the
# attempt, never as a verdict and never as a review state. The authority
# on whether a formal review exists is GitHub, read by the poll.
_SAME_ACCOUNT_RE = re.compile(
    r"(?i)can ?not (?:approve|request changes on) your own pull request")


def _publication_note(log: str) -> str | None:
    """Whether the reviewer's verdict reached GitHub as a formal review,
    as far as its own log can say.

    Deliberately narrow: this is a note on bench's verdict record saying
    what happened when the run tried to publish it, so that "bench found
    this" and "GitHub holds this" stay two facts. It decides nothing — a
    gate asking for a formal GitHub state reads GitHub's own answer.
    """
    if not log:
        return None
    if _SAME_ACCOUNT_RE.search(log):
        return ("refused-same-account: GitHub would not take a formal review "
                "from the account that opened the pull request, so the "
                "verdict is bench's own record and not a GitHub review "
                "state")
    return None


def _pr_provenance(agent_id: str, pinned: dict, moved: dict, log: str) -> dict:
    """What a PR review's record says it was of (task 168): the full head
    and base bench pinned and who said so, the pull request, whether its
    head had moved by the time the review ended, and the checks this run
    asked bench for. Read off bench's own records — the run's words are
    in the report beneath, and decide none of this."""
    head = dict(pinned.get("head") or {})
    base = dict(pinned.get("base") or {})
    label = "historical" if moved.get("historical") else (
        "superseded" if moved.get("stale") else "current")
    try:
        ran = [r.get("id") for r in evidence.for_agent(agent_id)
               if isinstance(r.get("id"), str)]
    except (OSError, ValueError, AttributeError):
        ran = []
    return {"kind": "pr",
            "head": {k: head.get(k) for k in ("ref", "commit", "authority")} if head else None,
            "base": {k: base.get(k) for k in ("ref", "commit", "authority")} if base else None,
            "pr": pinned.get("pr"), "prNumber": pinned.get("number"),
            "prState": pinned.get("state"), "label": label if pinned else None,
            "note": (moved.get("changed") or [None])[0],
            "published": _publication_note(log), "evidence": ran[:20]}


def _review_heading(pinned: dict, moved: dict) -> str:
    """What the permanent record calls this review.

    The heading carries the revision, because the task file outlives the
    checkout, the run and the pull request — and a reader a month later
    has nothing else to tell "this judged the code that merged" from
    "this judged a revision that was then pushed over". A historical
    review says so in the heading rather than in a sentence somebody has
    to reach.
    """
    if not pinned:
        return "PR review"
    head = ((pinned.get("head") or {}).get("commit") or "")[:12]
    at = f"PR review at {head}" if head else "PR review"
    if moved.get("historical"):
        return f"{at} (historical — the PR is " \
               f"{str(moved.get('state') or 'closed').lower()})"
    if moved.get("stale"):
        return f"{at} (superseded — the PR's head has moved since)"
    return at


def _reap_review(agent_id: str, proc: subprocess.Popen, log_file) -> None:
    record, stopped, rc = finish(agent_id, proc, log_file)
    filename = record["task"]

    verdict = None
    if rc == 0 and not stopped:
        try:
            text = Path(record["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        idx = text.find("RELEVANCE REVIEW")
        report = _report_of(record, text[idx:] if idx >= 0 else text)
        verdict = report.splitlines()[0] if report else None
        # a run launched before task 168 has no recorded launch input: its
        # binding is left out, and reads as unknown — never the brief now
        _file_report(record, "Relevance review", report, {
            "kind": "relevance", "briefRevision": record.get("briefRevision"),
            "briefSource": "launch" if record.get("briefRevision") else None})
        _session_report(record, report)

    name = record.get("name") or "the review"
    if stopped:
        summary = f"{name}'s check of {filename} was held"
    elif rc != 0:
        summary = f"{name}'s check of {filename} exited rc={rc} — {died_on(record)}"
    else:
        summary = f"{name} on {filename}: {verdict[:140] if verdict else 'report appended to the task'}"
    state.record_board_event({"kind": "agent", "actor": "agent", "file": filename,
                              "summary": summary})
    state.broadcast({"type": "board"})
    state.broadcast({"type": "agents"})


def stop_agent(agent_id: str) -> dict:
    with state.LOCK:
        record = state.AGENTS.get(agent_id)
        if record is None:
            raise ValueError("unknown agent")
        if record["status"] != "running":
            raise ValueError("agent is not running")
        record["status"] = "stopped"
        proc = record["proc"]
    proc.terminate()
    # And whatever bench was running *for* it. Ownership, not tidiness: a
    # check still running on a card whose run has been held is work
    # nobody is waiting for, and letting it finish would file evidence
    # attributed to a run that had already stopped. It is recorded as
    # cancelled — never as a failure.
    verify.cancel_for_agent(agent_id)
    return _agent_public(record)


def agent_diff(agent_id: str) -> dict:
    with state.LOCK:
        record = state.AGENTS.get(agent_id)
    if record is None:
        raise ValueError("unknown agent")
    if not record.get("worktree"):
        return {"agent": agent_id, "files": []}
    result = config.git_run(record["worktree"], "diff", "--numstat", record["base"],
                            timeout=10)
    files = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            plus, minus, name = parts
            files.append({"file": name,
                          "plus": int(plus) if plus.isdigit() else 0,
                          "minus": int(minus) if minus.isdigit() else 0})
    return {"agent": agent_id, "files": files}
