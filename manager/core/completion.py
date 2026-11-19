"""Merge & clean up: the merge, its publication, the recovery record, the
card's cleanup and a phase's sweep — the four outcomes a completion is,
each waiting on the one before (task 115, WP07; moved verbatim out of
`github.py`, which task 116 renamed `changes.py`).

The pull request itself — opening it, polling it, what the host last said
about it — stays with `changes`, which this module asks through its public
accessors (`remote`, `gh_available`, `branch_of`, `woven`, `pr_snapshot`,
`pr_info`, `merge_pr`, `merge_is_conditional`, `forget_pr`) and never by
reaching into its registry. So nothing here holds a copy of `PR_STATE`.

Every git process this module starts goes through `_run`, its one
module-level seam: tests that stand in for a push, a branch delete or a
remote read patch `completion._run`. A host merge is the hosting
provider's verb (`changes.merge_pr` → `githost.merge_change`).

The records it owns, both machine-local and both written durably, keep
the paths they had before the move, so a record written by an older board
is read and finished by this one:

- the **recovery record**, `<completions>/<stem>.json` — a merge that
  landed and has not been seen published;
- the **delivered record**, `<gates>/<stem>.delivered.json` — a completion
  this board finished, so a reopened card can say its earlier work was
  delivered.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import config
import drive as drive_mod
import gates
import changes
import githost
import plans
import records
import state
from taskfiles import member_entry, move_task, move_together, read_task


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """The one git seam: the same argv and environment `changes._run` builds
    (`config.git_argv`/`git_env`, so a read verb is hardened and a merge,
    push or worktree removal runs exactly as written), in the application
    unless `cwd` says otherwise. Raises what `subprocess.run` raises."""
    if cmd and cmd[0] == "git":
        root, rest = None, list(cmd[1:])
        if rest[:1] == ["-C"] and len(rest) >= 2:
            root, rest = rest[1], rest[2:]
        where = str(cwd or config.REPO)
        return subprocess.run(config.git_argv(root, *rest), cwd=where,
                              env=config.git_env(root or where, *rest),
                              capture_output=True, text=True, timeout=timeout)
    return subprocess.run(cmd, cwd=str(cwd or config.REPO),
                          capture_output=True, text=True, timeout=timeout)


def _branch_exists(branch: str) -> bool:
    return _run(["git", "rev-parse", "--verify", "--quiet", branch]).returncode == 0


def _contains(branch: str, tip: str) -> bool:
    """Is `branch` already in `tip`? The same question phases.py asks of a
    member — a merge leaves no record but this one, and it is the only
    thing that can say a phase's ending speaks for a particular card."""
    return _run(["git", "merge-base", "--is-ancestor", branch, tip]).returncode == 0


# ── what a finished completion leaves behind ────────────────────────────


def _delivered_path(filename: str) -> Path:
    """Beside the PR snapshot, where it has always been."""
    return config.GATES_DIR / f"{filename[:-3]}.delivered.json"


def delivered_record(filename: str) -> dict | None:
    """The completion this board finished for a card: what it merged, into
    which target, and when. Written once the ticket has moved, so a card
    reopened afterwards can say its earlier work was delivered."""
    try:
        data = json.loads(_delivered_path(filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("commit") else None


def _record_delivered(filename: str, commit: str | None, target: str,
                      how: str) -> None:
    if not commit:
        return
    try:
        records.write_json(_delivered_path(filename),
                           {"v": 1, "task": filename, "commit": commit,
                            "target": target, "how": how, "at": time.time()},
                           durable=True)
    except OSError:
        pass


# ── merge & clean up ────────────────────────────────────────────────────


def complete_task(filename: str, stage: str, *, expect_head: str | None = None,
                  actor: str = "you") -> dict:
    """The user chose "merge & clean up" on a move to done: park the drive
    if it is this task's, merge the branch, remove the worktree and
    branches, then move the card. Every step narrates; a conflict aborts
    cleanly and the card stays.

    Where the merge happens is `config.app_merge_on_origin()` and nothing
    else — never ticket sync read on its own. The local merge merges into
    the application's base in this checkout and pushes it; the merge on
    origin is `gh pr merge`, so local base only ever fast-forwards to it.

    Merging, publishing, cleaning up and moving the ticket are four
    outcomes, not one. Nothing is cleaned up and the card does not move
    until the application remote is *seen* to hold the merged commit (or
    there is no remote, which is a local-only completion and says so). A
    merge whose publication failed or could not be confirmed is recorded
    (`recovery`), so the retry — after a restart too — finishes it from
    what the remote actually holds rather than merging again.

    None of it is quick, and all of it is destructive, so the card is
    claimed before the first step and given back in a `finally` after the
    last one. The claim is what the card wears while this runs and what
    refuses a second request; see `state.claim_completing`.

    `expect_head` is the commit a delegated merge (task 142) was decided
    about. With it the merge takes exactly that commit — on an ungated
    board too, where a person's click merges the branch as it stands —
    and refuses, with nothing merged, when the branch or the gate's
    reading names another. `actor` is who the card's move into `done/`
    is attributed to: a person's click, or the orchestrator's own
    automated move.
    """
    if stage not in config.STAGE_DIRS or stage == "done":
        raise ValueError("complete runs on a live-stage card")
    if not (config.TASKS / stage / filename).is_file():
        raise ValueError(f"{filename} is not in {stage}/ — refresh the board")

    if not state.claim_completing(filename, "merging and cleaning up…"):
        raise ValueError(f"{filename} is already being completed — the card "
                         f"is showing each step; nothing was started twice")
    try:
        return _complete(filename, stage, expect_head, actor)
    finally:
        # every exit: merged, conflicted, on the wrong branch, or crashed
        state.release_completing(filename)


def _complete(filename: str, stage: str, expect_head: str | None = None,
              actor: str = "you") -> dict:
    """The steps themselves, run under the claim complete_task holds.

    Four outcomes, told apart: the merge into the base, its publication on
    the application remote, the cleanup of the card's workspace, and the
    ticket's move. Each later one waits for the one before it, and the
    result says which of them happened.
    """
    stem = filename[:-3]
    branch = changes.branch_of(filename)   # a phase's own branch, or task/<stem>
    task = changes.woven(filename, stage)
    # A completion that merged and then could not publish left a record. A
    # retry finishes *that* — the commit it merged, into the target it
    # merged into, the way it merged — or refuses when the situation has
    # changed under it, rather than quietly finishing something else.
    pending = recovery(filename)
    on_origin = False
    resuming = False
    if pending:
        on_origin = config.app_merge_on_origin()
        _assert_same_situation(filename, task, pending, on_origin)
        resuming = _merge_landed(pending, on_origin)
    if not resuming:
        # A pull request already merged on GitHub, outside this board: the
        # gate was about a merge this board would make, and there is none
        # to make. Finishing it is administrative — publication is read
        # off the remote, the base catches up and the workspace is
        # cleared — so obsolete checks, reviews and sign-off are not asked
        # for. Confirmed against the remote itself, never the snapshot
        # alone.
        landed = _merged_elsewhere(filename, task, branch)
        if landed:
            pending = {"commit": landed,
                       "target": plans.pr_base(filename, task),
                       "mode": "origin"}
            on_origin = True
            resuming = True
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"{filename}'s PR was merged on GitHub at "
                            f"{landed[:12]} — reconciling: nothing is merged "
                            f"again and no merge rule is asked for")})
    # Before the drive is parked, and well before anything irreversible:
    # what this board's acceptance gate requires, and whether the
    # evidence on record satisfies it against the commit this would
    # merge. A refusal here leaves the card, its branch, its worktree and
    # every record exactly as they are, and says the same sentence the
    # page says — one reading, one authority. A retry whose merge already
    # landed is past the gate: the gate was about the merge, which was of
    # the commit it read, and the merge cannot be taken back.
    scope = gates.scope_for(task)
    if not resuming:
        gates.require(gates.evaluate(filename, scope, task=task),
                      "merge & clean up")
    # Read before anything is destroyed and acted on only after the merge
    # lands: the phase branch is deleted in the middle of this, and it is
    # the only thing that can say which members it carried.
    brought = _brought(task, branch) if task.get("isPhase") else []

    # 1. the app must not keep running code that is about to be merged away
    _park_drive(filename)

    merged = False
    swept: list[dict] = []
    held: list[dict] = []
    unmerged: dict | None = None
    publication: dict | None = None
    cleanup: dict | None = None
    if _branch_exists(branch) or pending:
        # The same target the whole card has used, validated once more
        # before the one irreversible step: a target that has disappeared
        # is a changed situation rather than a licence to pick a new one,
        # and a card stacked on another branch is not this action's to
        # finish at all.
        target = plans.pr_base(filename, task)
        gone = plans.target_problem(filename, target)
        if gone:
            raise ValueError(gone)
        _assert_lands_in_the_base(filename, target)
        if resuming:
            pinned = pending["commit"]
            merged_tip = pinned
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"retrying the publication of {target} — "
                            f"{pinned[:12]} is already merged "
                            + ("on origin" if on_origin else "in this checkout")
                            + "; nothing is merged again")})
        else:
            # And again, immediately before the one irreversible step. The
            # first reading was before the drive was parked, which takes as
            # long as it takes; a push landing in between is a different
            # commit, and this is the read that is actually about the
            # commit the merge will take.
            decision = gates.evaluate(filename, scope, task=task)
            gates.require(decision, "the merge")
            # Reading twice narrows the window; it does not close it. So
            # the commit this reading validated is the one the merge takes
            # — the local merge by SHA, origin's with `--match-head-commit`
            # — and a branch that moves after this line is a refusal or a
            # kept branch, never an unverified commit in the base.
            pinned = _pinned_head(decision, branch)
            if expect_head:
                now = _tip(branch)
                if (pinned and pinned != expect_head) or now != expect_head:
                    raise ValueError(
                        f"{branch} is at {(pinned or now or 'no commit')[:12]}, "
                        f"not {expect_head[:12]}, the commit this merge was "
                        f"decided about — nothing was merged")
                pinned = expect_head
            on_origin = config.app_merge_on_origin()
            merged_tip = pinned or _tip(branch)
            # (an ungated board pins nothing, and calls them exactly as before)
            pin = (pinned,) if pinned else ()
            if on_origin:
                _merge_on_origin(filename, stage, branch, *pin)
            else:
                _merge_locally(filename, branch, *pin)
            # Written the moment the merge has landed and before anything
            # is published: a board that dies from here on comes back
            # knowing what it merged, and a retry reconciles from that.
            _remember(filename, {
                "branch": branch, "target": target,
                "mode": "origin" if on_origin else "local",
                "remote": changes.remote() or "", "commit": merged_tip,
                "publication": "pending", "detail": ""})
        merged = True

        publication = _publish(filename, target, merged_tip, on_origin)
        if publication["state"] not in ("published", "local-only"):
            # Merged, and not delivered. Nothing below runs: the branch,
            # the worktree, the remote branch and the phase's members are
            # exactly what a retry, or a person, needs to finish it.
            _remember(filename, {
                "branch": branch, "target": target,
                "mode": "origin" if on_origin else "local",
                "remote": publication["remote"], "commit": merged_tip,
                "publication": publication["state"],
                "detail": publication["detail"],
                "diverged": publication.get("diverged", False)})
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": _held_sentence(filename, stage, publication,
                                          on_origin)})
            state.broadcast({"type": "board"})
            return {"merged": True, "published": False,
                    "publication": publication, "moved": False,
                    "stage": stage, "swept": [],
                    "recovery": recovery_public(filename)}
        if on_origin:
            _catch_up_base(filename)

        # Nothing uncommitted is thrown away — the same rule a merged
        # phase member's workspace is cleared under. The work is in the
        # base branch by now, so nothing in there is *needed*; that is a
        # judgement about committed files, and an uncommitted change is
        # exactly what it does not cover. Nor is a commit made after the
        # gate read the head: the branch holding it is kept.
        cleanup, unmerged = _clean_up(filename, branch, pinned, merged_tip,
                                      on_origin,
                                      landed=[m["file"] for m in brought])
        if not unmerged:
            # a branch with unmerged commits still has a PR worth watching
            changes.forget_pr(filename)

        # the merge is published, so the members' work is in the base too
        swept, held = _sweep(filename, task, brought, merged_tip)

    result = {"merged": merged, "swept": [card["file"] for card in swept]}
    if publication:
        result["published"] = publication["state"] == "published"
        result["publication"] = publication
    if cleanup:
        result["cleanup"] = cleanup
    if held:
        result["kept"] = [card["file"] for card in held]
    if unmerged:
        # `done/` means the card's work is in the base, and its newest
        # commits are not: the gated commit merged, the card stays where
        # it was, and the completion says it was partial rather than
        # leaving `merged` to be read as accepted.
        forget_recovery(filename)
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{filename} partly completed — merged "
                        f"{unmerged['merged'][:12]}, the commit the gate read; "
                        f"{unmerged['branch']} has moved on to "
                        f"{unmerged['tip'][:12]}, which is not merged, so the "
                        f"card stays in {stage}/")})
        state.broadcast({"type": "board"})
        result.update({"partial": True, "stage": stage, "unmerged": unmerged,
                       "moved": False})
        return result
    move_task(filename, stage, "done", actor=actor)
    if merged and publication:
        _record_delivered(filename, publication.get("commit"),
                          publication.get("target") or "",
                          "origin" if on_origin else "local")
    # Only once the ticket has moved: until then a retry needs to know
    # what was merged, and a cleanup that stopped half way is finished by
    # the same retry rather than by guessing.
    forget_recovery(filename)
    result["moved"] = True
    state.broadcast({"type": "board"})
    return result


# ── a completion that merged and has not published yet ──────────────────


def _recovery_dir() -> Path:
    """Beside the recorded plans unless configured: the record finishes
    the work a plan started, and is machine-local for the same reason."""
    return config.COMPLETIONS_DIR or config.PLANS_DIR.parent / "completions"


def _recovery_path(filename: str) -> Path:
    return _recovery_dir() / f"{filename[:-3]}.json"


def recovery(filename: str) -> dict | None:
    """What an unfinished completion of this card merged, and where its
    publication stands — or None. Machine-local (`local/state/`), like the
    plan it sits beside: it is this board's reading of its own checkout and
    its remote, and nothing in it belongs in a ticket somebody pulls."""
    try:
        data = json.loads(_recovery_path(filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("commit"):
        return None
    return data


def _remember(filename: str, record: dict) -> None:
    """Write the record whole or not at all: a temporary file, then
    `os.replace`, so a board killed mid-write leaves the previous one."""
    stored = dict(record, task=filename, at=time.time())
    records.write_json(_recovery_path(filename), stored, durable=True)


def forget_recovery(filename: str) -> bool:
    """Drop the record — the completion finished, or a person decided what
    the unfinished one means. It touches no git: nothing is merged, pushed,
    reset or removed by forgetting."""
    try:
        _recovery_path(filename).unlink()
        return True
    except OSError:
        return False


_RECOVERY_KEYS = ("branch", "target", "mode", "remote", "commit",
                  "publication", "detail", "diverged", "at")


def recovery_public(filename: str) -> dict | None:
    found = recovery(filename)
    return {key: found.get(key) for key in _RECOVERY_KEYS} if found else None


def recoveries() -> dict:
    """Every card with an unfinished completion, for the board's state —
    a directory listing, and empty on a board where nothing failed."""
    directory = _recovery_dir()
    if not directory.is_dir():
        return {}
    found = {}
    for path in sorted(directory.glob("*.json")):
        name = f"{path.stem}.md"
        public = recovery_public(name)
        if public:
            found[name] = public
    return found


def _assert_same_situation(filename: str, task: dict, pending: dict,
                           on_origin: bool) -> None:
    """A retry finishes the completion that was started, or nothing.

    The record names the target the merge went into and the way it went.
    If either has changed since — a replan, an edited `BOARD_APP_MERGE` —
    finishing it now would publish one thing and clean up after another,
    so it is refused and a person decides which reading is right."""
    target = plans.pr_base(filename, task)
    mode = "origin" if on_origin else "local"
    was = pending.get("target") or target
    how = pending.get("mode") or mode
    if was == target and how == mode:
        return
    changed = (f"it now targets {target}" if was != target
               else f"this board now merges {'on origin' if on_origin else 'locally'}")
    raise ValueError(
        f"{filename} was merged into {was} ({how}) at "
        f"{str(pending.get('commit'))[:12]} and its publication is unfinished, "
        f"but {changed}. Nothing was done: publish {was} yourself, or discard "
        f"the unfinished-completion record from the merge sheet once you have "
        f"decided which is right")


def _merge_landed(pending: dict, on_origin: bool) -> bool:
    """Is the merge a record describes already in place? Locally that is
    the checkout's own base; on origin it is what the remote holds."""
    commit, target = pending.get("commit") or "", pending.get("target") or ""
    if not commit or not target:
        return False
    if not on_origin:
        return bool(_tip(commit)) and _contains(commit, target)
    rname = changes.remote()
    return bool(rname) and _remote_holds(rname, target, commit)[0] is True


def _remote_holds(rname: str, target: str, commit: str) -> tuple:
    """Does `target` on the application remote contain `commit`?

    Read from the remote itself — `ls-remote`, then a fetch of that exact
    branch when its tip is not here yet — never from a remote-tracking
    ref, which says what the remote held when somebody last looked.
    Returns (answer, the remote's tip, why not): the answer is None when
    the remote could not be read at all, which is *not* a no.
    """
    listed = _run(["git", "ls-remote", rname, f"refs/heads/{target}"],
                  timeout=60)
    if listed.returncode != 0:
        why = ((listed.stderr.strip() or "git gave no detail")
               .splitlines() or [""])[-1]
        return None, "", f"{rname} could not be read ({why[:120]})"
    words = listed.stdout.split()
    if not words:
        return False, "", f"{rname} has no {target} branch"
    tip = words[0]
    if tip == commit:
        return True, tip, ""
    if not _tip(tip):
        _run(["git", "fetch", "--no-tags", rname, f"refs/heads/{target}"],
             timeout=120)
    if not _tip(tip):
        return None, tip, (f"{rname}/{target} is at {tip[:12]}, which could not "
                           f"be fetched to compare")
    if _contains(commit, tip):
        return True, tip, ""
    return False, tip, f"{rname}/{target} is at {tip[:12]}, which does not contain it"


def _park_drive(filename: str) -> None:
    """The app must not keep running code whose workspace is about to go:
    park the drive when it is this card's, and wait for it to stop."""
    d = drive_mod.DRIVE
    if d and d.get("task") == filename and d.get("status") in ("starting", "up"):
        drive_mod.stop()
        for _ in range(40):
            if not drive_mod.alive(d):
                break
            time.sleep(0.5)


def remote_holds(rname: str, target: str, commit: str) -> tuple:
    """`(answer, the remote's tip, why not)` — does `target` on the
    application remote contain `commit`, read off the remote itself. The
    answer is None when the remote could not be read, which is not a no."""
    return _remote_holds(rname, target, commit)


def finish_delivered(filename: str, stage: str, branch: str, head: str,
                     landing: str, *, actor: str = "you",
                     how: str = "stack") -> dict:
    """Clean up a stacked card whose work the application base on the
    remote already holds (task 119: **Finish: clean up**), and move it to
    `done/`. Nothing is merged: the caller proved delivery with
    `remote_holds` and holds the card's completing claim. The workspace is
    cleared under `_clean_up`'s rules — uncommitted work keeps the
    worktree and its branch, and a branch that moved past `head` keeps the
    card where it is."""
    _park_drive(filename)
    cleanup, unmerged = _clean_up(filename, branch, head, head, True)
    if unmerged:
        return {"moved": False, "cleanup": cleanup,
                "why": (f"{branch} has moved on to {unmerged['tip'][:12]}, "
                        f"which is not delivered")}
    changes.forget_pr(filename)
    move_task(filename, stage, "done", actor=actor)
    _record_delivered(filename, head, landing, how)
    forget_recovery(filename)
    state.broadcast({"type": "board"})
    return {"moved": True, "cleanup": cleanup}


def complete_delivered(filename: str, stage: str, head: str, *,
                       actor: str = "orchestrator") -> dict:
    """Finish a card whose exact head the application remote already
    holds in its target — a merge whose reply was lost, or one made on
    the host meanwhile (task 142). Nothing is merged: delivery is read off
    the remote itself, the workspace is cleared under `_clean_up`'s rules
    and the card moves. A stacked card is refused as merge & clean up
    refuses it, and a remote that does not (or cannot) say it holds the
    commit is a refusal, never a guess."""
    if stage not in config.STAGE_DIRS or stage == "done":
        raise ValueError("complete runs on a live-stage card")
    if not state.claim_completing(filename, "reconciling a merge that landed…"):
        raise ValueError(f"{filename} is already being completed — nothing "
                         f"was started twice")
    try:
        task = changes.woven(filename, stage)
        branch = changes.branch_of(filename)
        target = plans.pr_base(filename, task)
        _assert_lands_in_the_base(filename, target)
        rname = changes.remote()
        if not rname:
            raise ValueError("this application has no remote, so delivery "
                             "cannot be read off one — nothing was done")
        held, _tip_now, why = _remote_holds(rname, target, head)
        if held is not True:
            raise ValueError(f"{rname}/{target} is not seen to hold "
                             f"{head[:12]}" + (f": {why}" if why else "")
                             + " — nothing was done")
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{rname}/{target} already holds {head[:12]} — "
                        f"reconciling {filename}: nothing is merged again")})
        return {"merged": False, "reconciled": True,
                **finish_delivered(filename, stage, branch, head, target,
                                   actor=actor, how="reconciled")}
    finally:
        state.release_completing(filename)


def _publish(filename: str, target: str, commit: str, on_origin: bool) -> dict:
    """Deliver the merge, and believe only the remote about whether it was.

    The local merge pushes the base; the merge on origin already happened
    there. Either way the answer is what the application remote — the one
    `config.git_remote()` names, never the work state's — holds afterwards:
    a push that reported failure but landed (a lost reply) is published,
    and one that reported success while the remote does not hold the
    commit is not. No remote at all is a local-only completion, said in
    those words rather than dressed as a publication.
    """
    rname = changes.remote()
    out = {"mode": "origin" if on_origin else "local", "remote": rname or "",
           "target": target, "commit": commit, "state": "", "detail": "",
           "remoteTip": "", "diverged": False}
    if not rname:
        if on_origin:
            out.update(state="unconfirmed",
                       detail="this application has no remote to confirm "
                              "the merge against")
        else:
            out.update(state="local-only",
                       detail="this application has no remote, so nothing "
                              "was published")
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"{target} is local-only — no application remote "
                            f"is configured, so nothing was pushed or merged "
                            f"on GitHub")})
        return out
    pushed = None
    if not on_origin:
        pushed = _run(["git", "push", rname,
                       f"refs/heads/{target}:refs/heads/{target}"], timeout=180)
    holds, tip, why = _remote_holds(rname, target, commit)
    out["remoteTip"] = tip
    refused = ""
    if pushed is not None and pushed.returncode != 0:
        lines = [line.strip() for line in (pushed.stderr.strip()
                 or pushed.stdout.strip() or "git gave no detail").splitlines()]
        # what the remote itself said (a protection rule, a hook) and the
        # rejection line, ahead of git's own boilerplate
        said = [line for line in lines if line.startswith(("remote:", "!"))]
        refused = " ".join(said or lines[-3:])[:240]
    if holds is True:
        out["state"] = "published"
        if refused:
            said = (f"the push of {target} reported a failure, but {rname}/"
                    f"{target} holds {commit[:12]} — it landed and the reply "
                    f"was lost ({refused[:100]})")
        elif on_origin:
            said = f"confirmed: {rname}/{target} holds {commit[:12]}"
        else:
            said = (f"pushed {target} to {rname} — {rname}/{target} holds "
                    f"{commit[:12]} (PR marked merged)")
        state.record_board_event({"kind": "agent", "actor": "board",
                                  "file": filename, "summary": said})
        return out
    out.update(state="failed" if refused else "unconfirmed",
               detail=" · ".join(part for part in (refused, why) if part))
    if holds is False and tip and _tip(tip) and not _contains(tip, target):
        # the remote has commits this checkout's base does not: a push
        # cannot fast-forward it, and nothing here will force it
        out["diverged"] = True
    return out


def _held_sentence(filename: str, stage: str, publication: dict,
                   on_origin: bool) -> str:
    """What the ticker says about a merge that did not publish: what
    already happened, what did not, and what a person does next."""
    target, rname = publication["target"], publication["remote"] or "the remote"
    commit = publication["commit"][:12]
    where = ("GitHub reported the merge" if on_origin
             else f"merged into {target} in this checkout")
    what = ("but the push failed" if publication["state"] == "failed"
            else "but its publication could not be confirmed")
    sentence = (f"{filename}: {where} at {commit}, {what} — {rname}/{target} "
                f"does not hold it ({publication['detail'][:160]}). Nothing "
                f"was cleaned up and the card stays in {stage}/")
    if publication.get("diverged"):
        sentence += (f"; {rname}/{target} has commits this checkout does not — "
                     f"bring them into {target} (pull, then merge) and retry")
    else:
        sentence += "; resolve it, then merge & clean up again"
    return sentence


def _clean_up(filename: str, branch: str, pinned: str | None, merged: str,
              on_origin: bool, landed: list[str] | tuple = ()
              ) -> tuple[dict, dict | None]:
    """The card's own workspace, after its merge is published. Returns what
    became of each part — never "removed" for something git kept — and the
    unmerged tip when the branch moved past the commit the gate read."""
    stem = filename[:-3]
    worktree = config.WORKTREES / stem
    out = {"worktree": "absent", "branch": "absent", "remoteBranch": "none",
           "notes": []}
    unmerged = None
    kept = ""
    now = _tip(branch)
    if pinned and now and now != pinned:
        kept = (f"{branch} has moved on to {now[:12]} since the gate read "
                f"{pinned[:12]}, and those commits were not merged")
        # What merged is a historical head; the card's newest work is
        # not in the base, so the card is not Done.
        unmerged = {"branch": branch, "merged": pinned, "tip": now}
        if worktree.exists():
            out["worktree"] = "kept"
    elif worktree.exists():
        out["worktree"] = "kept"
        dirty = config.dirty_files(worktree)
        if dirty:
            kept = (f"{len(dirty)} uncommitted change"
                    f"{'' if len(dirty) == 1 else 's'} in it")
        else:
            removed = _run(["git", "worktree", "remove", str(worktree)])
            if removed.returncode != 0:
                kept = ((removed.stderr.strip() or removed.stdout.strip()
                         or "git would not remove it").splitlines()[-1][:120])
            else:
                out["worktree"] = "removed"
    if kept:
        out["notes"].append(kept)
        if now:
            out["branch"] = "kept"
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"worktree for {stem} kept — {kept}; the branch "
                       f"is kept with it"})
    elif now:
        # -D when origin made the merge: the base here has not caught up
        # yet, so the safe delete would refuse something landed.
        deleted = _run(["git", "branch", "-D" if on_origin else "-d", branch])
        if deleted.returncode == 0:
            out["branch"] = "deleted"
            # The plan described work that is now in the base, on a branch
            # that has just gone. A workspace *kept* keeps its plan,
            # because the branch it names is still the one holding the work.
            plans.forget(filename)
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"cleaned up: worktree and local branch for {stem} "
                            f"removed" if out["worktree"] == "removed" else
                            f"cleaned up: local branch {branch} removed")})
        else:
            out["branch"] = "kept"
            why = ((deleted.stderr.strip() or "git would not delete it")
                   .splitlines() or [""])[-1][:120]
            out["notes"].append(f"local branch kept — {why}")
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": f"{branch} kept — git would not delete it ({why})"})
    else:
        plans.forget(filename)        # a retry after the branch already went
    rname = changes.remote()
    if rname:
        if _delete_remote_branch(rname, branch, merged, filename,
                                 landed=landed):
            out["remoteBranch"] = "gone"
        else:
            out["remoteBranch"] = "kept"
    return out, unmerged


def completion_facts(filename: str, stage: str) -> dict:
    """What **merge & clean up** would do for this card, read from the same
    authorities the completion itself reads — the merge mode from
    `config.app_merge_on_origin()`, the target from the recorded plan, the
    stacked-card refusal from `_assert_lands_in_the_base`, the remote from
    `config.git_remote()` — so the sheet describes the completion that
    would run, not one inferred from ticket sync. Read-only: no fetch, no
    `gh` call, no write."""
    task = changes.woven(filename, stage)
    target = plans.pr_base(filename, task)
    saved = plans.recorded(filename)
    on_origin = config.app_merge_on_origin()
    rname = changes.remote()
    facts = {
        "file": filename, "branch": changes.branch_of(filename),
        "mode": "origin" if on_origin else "local",
        "target": target,
        "appBase": (saved or {}).get("appBase") or config.APP_BASE,
        "remote": rname or "",
        "publication": ("pr-merge" if on_origin
                        else "push" if rname else "none"),
        "ticketSync": bool(config.SYNC),
        "ticketsSeparate": not config.app_shares_state_repository(),
        "pr": task.get("pr") or "",
        "stacked": False, "refusal": "",
        "recovery": recovery_public(filename),
    }
    refusal = plans.target_problem(filename, target)
    if not refusal:
        try:
            _assert_lands_in_the_base(filename, target)
        except ValueError as exc:
            facts["stacked"] = True
            refusal = str(exc)
    if not refusal and on_origin and not facts["pr"] and not facts["recovery"]:
        refusal = (f"{filename} has no PR, and this board merges the "
                   f"application on origin — open one first (↑ open PR)")
    if not refusal and on_origin and not changes.gh_available():
        refusal = (f"{changes.host_unavailable()}, and this board merges the "
                   f"application on origin (BOARD_APP_MERGE)")
    facts["refusal"] = refusal or ""
    return facts


def _merged_elsewhere(filename: str, task: dict, branch: str) -> str | None:
    """The commit a pull request merged on GitHub delivered, or None.

    Asked only when the poll's snapshot says the PR was merged, and then
    of the remote itself (`_remote_holds`, bounded): the snapshot is what
    makes the question worth one remote read, and the remote is what
    answers it."""
    snap = changes.pr_snapshot(filename)
    if not snap or not snap.get("merged"):
        return None
    rname = changes.remote()
    tip = _tip(branch) or str(snap.get("head") or "")
    if not rname or not tip:
        return None
    target = plans.pr_base(filename, task)
    return tip if _remote_holds(rname, target, tip)[0] is True else None


def _brought(task: dict, phase_branch: str) -> list[dict]:
    """The members a phase actually merged — the only cards its ending
    speaks for.

    Two things have to be true, and they are the same pair phases.py reads
    a member as `merged` by: the card settled into `review/` or `done/`,
    and its branch is contained in the phase branch — or there was never a
    branch to bring, which is a member finished before the phase reached
    it. Containment alone is not enough, for the reason it is not enough
    there either: a run that exits without committing leaves an empty
    branch that is trivially contained.

    A member that halted, was held or was walked back satisfies neither.
    Its card stays where it is and so do its worktree and its branch:
    there is work in them, and it is the reason a person will look at this
    phase afterwards. Removing a worktree with work in it is the one
    unrecoverable thing here.
    """
    brought = []
    for member in task.get("members") or []:
        if member.get("stage") not in ("review", "done"):
            continue
        branch = f"task/{member['file'][:-3]}"
        if _branch_exists(branch) and not _contains(branch, phase_branch):
            continue
        brought.append(member)
    return brought


def _tip(ref: str) -> str:
    """The commit `ref` names right now, or "" when it names none."""
    found = _run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return found.stdout.strip() if found.returncode == 0 else ""


def _pinned_head(decision: dict, branch: str) -> str | None:
    """The commit a gated merge must take: the one the gate just validated.

    An ungated board merges the branch as it always did, so it pins
    nothing. A gated (or overridden) decision must name a commit on the
    very branch this would merge — a gate that read another ref, or no
    commit at all, has validated nothing this merge could take.
    """
    if decision.get("decision") == "ungated":
        return None
    head = decision.get("head") or {}
    commit = head.get("commit")
    if not commit or head.get("ref") != branch:
        raise ValueError(
            f"the gate read {head.get('ref') or 'no branch'} at "
            f"{str(commit or 'no commit')[:12]}, and this would merge "
            f"{branch} — nothing was merged, because a gate is only about the "
            f"commit it read")
    return commit


def _still_targeted(branch: str, filename: str,
                    landed: list[str] | tuple = ()) -> str | None:
    """Why `branch` must stay on the remote, or None: an open change
    request targets it, or a card on this board still aims its work at it,
    or the hosting provider could not say (task 117). Deleting the base of
    an open change is something a host may answer by closing or
    retargeting the dependant — what GitHub does is not assumed here — so
    the branch is kept, and the reason is said."""
    # The members a phase merged target its branch and are delivered with
    # it: they go to done/ in the same ending, so they hold nothing back.
    waiting = [f for f in plans.dependants(branch, skip=filename)
               if f not in landed]
    if waiting:
        return (f"{', '.join(waiting)} still "
                f"{'targets' if len(waiting) == 1 else 'target'} it")
    found = githost.changes_targeting(branch)
    if found is None:
        return (f"whether an open {githost.words()['change']} targets it "
                f"could not be established ({changes.host_unavailable()})")
    if found:
        return (f"an open {githost.words()['change']} targets it "
                f"({', '.join(c['url'] for c in found[:3])})")
    return None


def _delete_remote_branch(rname: str, branch: str, merged: str,
                          filename: str, landed: list[str] | tuple = ()
                          ) -> bool:
    """Delete `branch` on the remote only while it holds nothing but what
    was merged — read with `ls-remote`, then deleted with a lease on that
    exact value, so a push landing in between makes git refuse rather than
    bench delete a commit nobody merged — and only while nothing targets
    it (`_still_targeted`). Returns whether it was deleted."""
    listed = _run(["git", "ls-remote", rname, f"refs/heads/{branch}"], timeout=60)
    words = listed.stdout.split() if listed.returncode == 0 else None
    if words == []:
        return True                      # nothing there to delete
    targeted = _still_targeted(branch, filename, landed)
    if targeted:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"{branch} kept on {rname} — {targeted}"})
        return False
    tip = words[0] if words else ""
    if not tip or not (tip == merged or _contains(tip, merged)):
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{branch} kept on {rname} — it holds "
                        f"{tip[:12] or 'something bench could not read'}, "
                        f"which is not in what was merged")})
        return False
    pushed = _run(["git", "push", f"--force-with-lease=refs/heads/{branch}:{tip}",
                   rname, "--delete", branch], timeout=60)
    if pushed.returncode != 0:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{branch} kept on {rname} — it moved while bench was "
                        f"deleting it ({pushed.stderr.strip()[:100]})")})
        return False
    return True


def _clear_member(member: dict, merged: str | None = None) -> str:
    """One merged member's workspace, cleared exactly as completing an
    ordinary card clears its own: the worktree, the local branch, the
    branch on the remote. Returns what to say when something was kept.

    The worktree comes out without `--force`, which is the whole
    difference from the card's own cleanup above. A member's work is in
    `main` by the time this runs, so nothing in there is *needed* — but
    that is a judgement about committed files, and an uncommitted change
    is exactly what it does not cover. A dirty worktree is reported and
    kept, with its branch, rather than forced: this is the one
    unrecoverable step in the ending, and it is asked before it is taken
    rather than left to git to refuse.
    """
    stem = member["file"][:-3]
    branch = f"task/{stem}"
    worktree = config.WORKTREES / stem
    # A member branch that gained commits after the phase merged it holds
    # work the merge did not carry; clearing it would delete that.
    if merged and _branch_exists(branch) and not _contains(branch, merged):
        return (f"{stem}: worktree and branch kept — {branch} has moved on "
                f"since the phase merged it")
    try:
        if worktree.exists():
            dirty = _run(["git", "status", "--porcelain"],
                         cwd=worktree).stdout.split("\n")
            dirty = [line for line in dirty if line.strip()]
            if dirty:
                return (f"{stem}: worktree kept — {len(dirty)} uncommitted "
                        f"change{'' if len(dirty) == 1 else 's'} in it")
            removed = _run(["git", "worktree", "remove", str(worktree)])
            if removed.returncode != 0:
                detail = (removed.stderr.strip() or removed.stdout.strip()
                          or "git would not remove it")
                return f"{stem}: worktree kept — {detail.splitlines()[-1][:120]}"
        if _branch_exists(branch):
            # -D when origin made the merge, for the same reason the
            # card's own branch takes it: the base here has not caught up.
            _run(["git", "branch",
                  "-D" if config.app_merge_on_origin() else "-d", branch])
            rname = changes.remote()
            if rname and merged:
                _delete_remote_branch(rname, branch, merged, member["file"])
            elif rname:
                targeted = _still_targeted(branch, member["file"])
                if targeted:
                    state.record_board_event({
                        "kind": "agent", "actor": "board",
                        "file": member["file"],
                        "summary": f"{branch} kept on {rname} — {targeted}"})
                else:
                    _run(["git", "push", rname, "--delete", branch], timeout=60)
    except (subprocess.SubprocessError, OSError) as exc:
        return f"{stem}: workspace kept — {str(exc)[:120]}"
    changes.forget_pr(member["file"])
    plans.forget(member["file"])
    return ""


def _moved_on(member: dict, merged: str | None) -> bool:
    """Has this member's branch gained commits the phase's merge did not
    carry? Then its newest work is not in the base, whatever its card says."""
    branch = f"task/{member['file'][:-3]}"
    return bool(merged and _branch_exists(branch) and not _contains(branch, merged))


def _sweep(filename: str, task: dict, brought: list[dict],
           merged: str | None = None) -> tuple[list[dict], list[dict]]:
    """Finish the cards the phase finished.

    A member stops at `review/` on purpose — `done/` has always meant
    merged into `main`, and merged into a phase branch is not that. This
    is the moment it becomes that: the phase's PR is in `main`, so every
    card it carried is too, and leaving them in the column whose note is
    "your move" would hand back a pile of work already judged.

    One ending, so it is told as one: the cards move together in a single
    commit (`taskfiles.move_together`) and the ticker gets one line naming
    the phase and how many went with it, rather than five moves scrolling
    past. What was *not* swept is named too — a phase that left cards
    behind is a phase somebody still has to look at.

    A member whose branch moved on after the phase merged it is the one
    exception among those brought: only part of its work is in the base,
    so its workspace is kept (`_clear_member`) and so is its card, where it
    is. Returns (moved, held) — the cards that went to done/, and those.
    """
    members = task.get("members") or []
    if not members:                      # a phase card listing nobody
        return [], []
    held = [member for member in brought if _moved_on(member, merged)]
    whole = [member for member in brought if member not in held]
    kept = [note for note in (_clear_member(member, merged) for member in brought)
            if note]
    moved = move_together([(member["file"], member["stage"]) for member in whole
                           if member["stage"] != "done"],
                          "done", f"with phase {task['number'] or filename[:-3]}")
    left = len(members) - len(brought)
    name = (member_entry(task["number"], task["title"]) if task["number"]
            else task["title"])
    summary = f"phase {name} finished — "
    summary += (f"{_cards(len(whole))} merged into it went to done/, worktrees "
                f"and branches cleaned up" if whole
                else "it had merged nothing, so no card went with it"
                if not held else "no card went to done/ with it")
    for member in held:
        summary += (f" · {member['file'][:-3]} has moved on since the phase "
                    f"merged it, so only part of it is merged and it stays in "
                    f"{member['stage']}/")
    if left:
        summary += (f" · {_cards(left)} it never merged "
                    + ("stays where it is" if left == 1 else "stay where they are"))
    state.record_board_event({"kind": "phase", "actor": "board",
                              "file": filename, "summary": summary})
    for note in kept:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"{note} — nothing uncommitted is thrown away"})
    return moved, held


def _cards(count: int) -> str:
    return f"{count} card{'' if count == 1 else 's'}"


def _assert_lands_in_the_base(filename: str, target: str) -> None:
    """`done/` has always meant merged into the application base, and a
    stacked card merged into its prerequisite is not that — exactly as a
    phase member merged into its phase branch is not that.

    So **merge & clean up** refuses a stacked card in both modes, and for
    one reason rather than two: not because the local path cannot do it
    (it cannot — it merges into whatever this checkout has out, which is
    how a stacked card's work lands in the default with nobody deciding
    it should) but because finishing it is not this action's to do. The
    stack lands when its prerequisite's own PR lands; then this card's
    target has gone, the board says so, and a replan onto the base is the
    person's decision that makes it finishable.

    The application base is the one the work was planned against, read
    from the record: a card launched on `main` before the operator moved
    `BOARD_APP_BASE` to `trunk` is not stacked on `main`, and finishing it
    into `main` is exactly what it was planned to do.
    """
    saved = plans.recorded(filename)
    app_base = (saved or {}).get("appBase") or config.APP_BASE
    if target == app_base:
        return
    raise ValueError(
        f"{filename} is stacked on {target}, not {app_base} — merge & "
        f"clean up would finish it without its work reaching "
        f"{app_base}. Land its PR into {target} first; once {target} "
        f"itself has merged, retarget the card (⌖ replan) and finish it here")


def _merge_locally(filename: str, branch: str, commit: str | None = None) -> None:
    """The local merge: merge into the application's own base branch in
    this checkout. Publishing it is a separate step (`_publish`), because a
    merge that landed here and a base that reached the remote are two
    different facts.

    The target is the card's recorded PR target, asked here rather than
    passed in so this function and the PR that was opened can never read
    different answers. `_complete` has already refused anything but the
    application base (see `_assert_lands_in_the_base`); reading it again
    rather than assuming it is what keeps the branch names below honest.

    `commit`, when a gate validated one, is what is merged — the SHA, not
    the branch, so a commit landing on the branch after the gate read it
    is not carried in. The message still names the branch.
    """
    target = plans.pr_base(filename)
    current = _run(["git", "branch", "--show-current"]).stdout.strip()
    if current != target:
        raise ValueError(f"the repo is on '{current}', not {target} — switch first")
    # Already in? Then there is nothing to merge, and saying "merged" would
    # describe work this attempt did not do — a retry after a crash
    # between the merge and its publication lands here.
    if _contains(commit or branch, target):
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"{branch} is already in {target} — nothing merged again"})
        return
    result = _run(["git", "merge", "--no-edit", "-m", f"Merge branch '{branch}'",
                   commit] if commit else
                  ["git", "merge", "--no-edit", branch], timeout=120)
    if result.returncode != 0:
        _run(["git", "merge", "--abort"])
        detail = (result.stdout.strip() or result.stderr.strip())[-160:]
        raise ValueError(f"merge conflict — resolve by hand ({detail})")
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"merged {branch} into {target}"})


def _merge_on_origin(filename: str, stage: str, branch: str,
                     commit: str | None = None) -> None:
    """Team mode: the merge commit is made by GitHub, on origin.

    Replicas keep converging only while local main advances by
    fast-forward, so the board never creates a merge commit of its own —
    it asks origin for one and lets the sync beat deliver it. Needs merge
    rights on the repo for whoever clicks, which the local path did not.

    `commit`, when a gate validated one, makes the merge conditional:
    `gh pr merge --match-head-commit` has GitHub refuse it if the pull
    request's head is anything else by the time it merges. A gh that
    cannot say that is refused rather than worked around — an
    unconditional merge over a gate is the race the gate exists to close.

    A reply can be lost — `gh` timing out on a merge GitHub made — so the
    remote's base is read before asking and again after a refusal: a merge
    already there is not asked for twice, and one that landed is not
    reported as refused. Whether it was *delivered* is `_publish`'s to
    say afterwards, from the remote, not from gh's exit status.
    """
    rname = changes.remote()
    intended = commit or _tip(branch)
    target = plans.pr_base(filename)

    def landed() -> bool:
        return bool(rname and intended) and \
            _remote_holds(rname, target, intended)[0] is True

    if landed():
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{rname}/{target} already holds {intended[:12]} — the "
                        f"PR was merged already, so it is not merged again")})
        return
    if commit and changes.gh_available() and not changes.merge_is_conditional():
        raise ValueError(
            f"this board's gate validated {commit[:12]}, and the installed gh "
            f"cannot make the merge conditional on it — `gh pr merge --help` "
            f"offers no --match-head-commit. Nothing was merged: upgrade gh, "
            f"because bench will not merge unconditionally over a gate")
    task = read_task(config.TASKS / stage / filename, stage)
    url = task.get("pr")
    if not url:
        raise ValueError(
            f"{filename} has no PR, and this board merges the application on "
            f"origin — open a PR for {branch} first (↑ open PR on the card)")
    if not changes.gh_available():
        raise ValueError(f"{changes.host_unavailable()} — this board merges "
                         f"the application on origin (BOARD_APP_MERGE)")
    number = url.rstrip("/").rsplit("/", 1)[-1]
    # origin merges the PR into whatever the PR targets, so the one thing
    # worth checking is that the PR targets what this card planned. A
    # contradiction is reported, never resolved by retargeting.
    target = plans.pr_base(filename, task)
    actual = (changes.pr_info(branch) or {}).get("base")
    if actual and actual != target:
        raise ValueError(
            f"{filename}'s PR is open against {actual}, and the card's plan "
            f"targets {target} — nothing was merged. Settle which base is right "
            f"(⌖ replan, or change the PR on GitHub) first")
    condition = ["--match-head-commit", commit] if commit else []
    try:
        result = changes.merge_pr(number, *condition, timeout=180)
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess([], 1, "", "gh timed out")
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[-160:]
        if landed():
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"gh reported a failure ({detail[:100]}), but "
                            f"{rname}/{target} holds {intended[:12]} — the merge "
                            f"landed and the reply was lost")})
            return
        raise ValueError(f"origin would not merge the PR — resolve it on GitHub ({detail})")
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": (f"merged {filename}'s PR on origin"
                    + (f" at {commit[:12]}" if commit else ""))})


def _catch_up_base(filename: str) -> None:
    """Bring the application's own base branch to the merge origin made.

    Vendored, the sync beat does this — it fetches and fast-forwards the
    one repository both identities share. With the state in a repository
    of its own, *nothing* does: sync never touches the application, and
    the checkout would sit behind a base it has never seen. New task
    branches are cut from `<remote>/<base>` after a fetch, so they are
    right either way; this is about the checkout a person reads and a
    driver runs in.

    Best effort, and narrated honestly: a checkout on another branch or
    with uncommitted changes is left exactly as it is.
    """
    base, rname = config.APP_BASE, changes.remote()
    if not rname or config.SYNC and config.app_shares_state_repository():
        return                      # the sync beat owns this checkout
    _run(["git", "fetch", rname, base], timeout=120)
    current = _run(["git", "branch", "--show-current"]).stdout.strip()
    if current != base:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"the application checkout is on '{current}', not "
                       f"{base} — it stays behind the merge origin made "
                       f"until you switch back and pull"})
        return
    result = _run(["git", "merge", "--ff-only", f"{rname}/{base}"], timeout=120)
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip()
                  or "git gave no detail").splitlines()[-1][:140]
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"could not fast-forward {base} to {rname}/{base} "
                       f"after the merge ({detail}) — pull it yourself"})
        return
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"fast-forwarded {base} to {rname}/{base}"})
