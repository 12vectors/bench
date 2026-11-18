"""Whether a stored verdict still holds — and whether a record of another
check may answer for this one.

Staleness is a property of the evidence, not of its age. A record with a
result identity holds while every dimension of it holds: `freshness()`
re-derives each one now and says which moved, by its own reason
(`source-changed`, `check-changed`, `runner-changed` …). A legacy record
— written before identities existed — is read with the rules it was
written under, `policy-changed` included, and is never rewritten.
`outcome_now()` is the one reading that may disagree with a stored
outcome, and only ever in the safe direction: a `passed` becomes `stale`,
and nothing becomes `passed` here.

Declared equivalence (card 104) lives here too, because it is the same
kind of question — may this record count, now — and the gate and the
reuse route must ask it of one function.

Moved out of `verify.py` verbatim by task 121 (WP12e). It is inside the
runner revision (`resultidentity.RUNNER_FILES`): how a stored pass is read
as holding is part of what the pass is worth. Its memos are cleared by
`forget()`, which `verify.forget_cached` calls with every other owner's.
"""

from __future__ import annotations

import shlex
import time
from pathlib import Path

import config
import evidence
import profiles
import resultidentity
import reviews

# Asking whether a pass is still fresh means reading the checkout it ran
# in: a handful of git commands and a hash per untracked file. That is the
# right price for a question somebody asked, and the wrong price to pay
# per card on every state payload — so the answer is memoised for
# FRESH_TTL seconds. A new record bumps `evidence.epoch()`, which the
# board-level memos key on; this memo is per record, and a terminal record
# never changes.
FRESH_TTL = 10.0
_FRESH: dict[str, tuple[float, dict]] = {}


# ── declared equivalence (design §5, card 104) ─────────────────────────
#
# A record of another check answers this one only when three things hold
# together, and never when any one of them holds alone: the operator
# declared it (`"accepts"` on this check, read from the policy in force
# now), the two definitions agree in every substantive dimension
# (`profiles.check_differences` — the name is the only thing allowed to
# differ, and the declared root only by name within its class), and the
# record itself ran in the root class this check would run in, with a
# result identity to show it. Everything else a pass depends on — the
# source, the programs and interpreters, the input contents, the declared
# environment's values and the runner — is the record's own result
# identity, judged exactly as it is for a record of this check's own name.

EQUIVALENCE_WHY = {
    "not-accepted": "a different check, which this check does not declare "
                    "that it accepts — names never make two checks the same",
    "legacy-record": "recorded before result identities existed, so it "
                     "cannot be shown to be the same check",
    "root-class": "it did not run in the kind of checkout this check runs in",
}


def accepted_now(check: dict) -> dict:
    """The checks `check` accepts in the policy as it reads *now*, by name,
    with their current definitions — never what a frozen manifest said.
    Empty when the document does not declare `check`, or accepts nothing:
    a declaration removed since a run was launched is removed."""
    try:
        doc = profiles.document()
        steps, _ = profiles.document_steps(doc)
        declared, _ = profiles.document_checks(doc, steps)
    except (OSError, ValueError):
        return {}
    current = declared.get(check.get("name"))
    if current is None:
        return {}
    return {name: declared[name] for name in current.get("accepts") or []
            if name in declared}


def equivalence(record: dict, check: dict, accepted: dict) -> dict:
    """Whether `record` — evidence filed under some check's name — may
    answer a requirement for `check`, and if not, which dimension stops it.

    `accepted` is what `check` accepts now, by name, with each current
    definition (`accepted_now`, or the gate policy's `acceptedChecks`). A
    record of `check`'s own name is trivially its own; this function is
    about the other names, and it fails closed at every step."""
    name = record.get("check")
    mine = check.get("name")
    if name == mine:
        return {"equivalent": True, "dimension": None, "why": None}

    def refuse(dimension: str, why: str) -> dict:
        return {"equivalent": False, "dimension": dimension, "why": why}

    other = accepted.get(name)
    if other is None:
        return refuse("not-accepted",
                      f"'{name}' is {EQUIVALENCE_WHY['not-accepted']} "
                      f"('{mine}')")
    differs = profiles.check_differences(check, other,
                                         record.get("scope") or None)
    if differs:
        return refuse(*differs[0])
    if record.get("outcome") in evidence.LIVE:
        # Still running: its identity is not written yet, and the two
        # definitions agree, so it is the newer answer in flight.
        return {"equivalent": True, "dimension": None, "why": None}
    identity = record.get("identity") if isinstance(record.get("identity"),
                                                   dict) else {}
    if not record.get("resultId") or not identity:
        return refuse("legacy-record",
                      f"the '{name}' record was "
                      f"{EQUIVALENCE_WHY['legacy-record']} as '{mine}'")
    want = ("worktree" if profiles.root_class_of(str(check.get("root")))
            == "worktree" else "fresh")
    klass = identity.get("rootClass")
    if klass != want:
        if klass == "worktree":
            why = (f"'{name}' ran in the working checkout, which can hold "
                   f"files a fresh checkout does not, and '{mine}' runs in a "
                   f"pinned one")
        elif want == "fresh":
            why = (f"'{name}' ran in a pinned checkout that already held files "
                   f"beyond its commit, so it was not a clean pinned checkout "
                   f"of that commit")
        else:
            why = (f"'{name}' ran in a fresh pinned checkout and '{mine}' runs "
                   f"in the card's working checkout — different root classes "
                   f"are distinct assurance")
        return refuse("root-class", why)
    return {"equivalent": True, "dimension": None, "why": None}
# ── is this evidence still about anything? ─────────────────────────────


def freshness(record: dict) -> dict:
    """What has changed since this verification ran.

    The question a pass only answers while its binding holds. Three
    bindings, and each is a different way for a pass to stop meaning
    anything: the **source** (the commit plus everything uncommitted), the
    **policy** (the resolved profile, which carries the check's own
    definition), and the **context** (the inputs the run was pinned).
    Compared, never resolved: this function moves nothing and re-runs
    nothing.
    """
    changed: list[str] = []
    # The same findings as slugs, in the same order, so a consumer branches
    # on data rather than on the prose beside it — the law `REASONS` was
    # written under, applied to the one place that had grown a string
    # match instead.
    reasons: list[str] = []

    def moved(slug: str, text: str) -> None:
        reasons.append(slug)
        changed.append(text)

    if not record:
        return {"fresh": True, "changed": [], "reasons": [], "stale": False}
    source = record.get("source") or {}
    where = record.get("root") or {}
    root = Path(where.get("path") or "")
    if not source.get("bound"):
        moved("source-changed",
              source.get("why") or
              "the source this ran against was never bound, so the "
              "result cannot be reused")
    elif where.get("policy") == "review":
        # A `review` check ran in a checkout bench pinned to one commit —
        # the head a pull request proposed — and then removed when the
        # review ended, on purpose. So the checkout being gone says
        # nothing, exactly as it says nothing for a `source` check. The
        # binding is the reviewed head, and the question is whether the
        # pull request still proposes it: a push that lands after the
        # check makes this evidence history rather than the new head's
        # pass. Read from what the PR poll last saw, so asking costs no
        # call to GitHub (`reviews.observe`).
        now = reviews.observed(record.get("task") or "") or {}
        current = str(now.get("commit") or "")
        if current and current != source.get("commit"):
            moved("head-changed",
                  f"the pull request's head has moved on — this ran against "
                  f"{str(source.get('commit') or '')[:12]} and the PR is now "
                  f"at {current[:12]}, so it says nothing about the code "
                  f"there now")
        elif str(now.get("state") or "").upper() in ("MERGED", "CLOSED"):
            moved("pr-ended",
                  f"the pull request has been "
                  f"{str(now.get('state')).lower()} since this ran, so it is "
                  f"a historical record of what was proposed rather than a "
                  f"current result")
    elif where.get("policy") == "source":
        # A `source` check ran in a *detached checkout of one commit* that
        # bench made and then removed on purpose, so the checkout being
        # gone says nothing — asking after it would make every read-only
        # verification stale the moment it finished. The commit is the
        # binding, and the question is whether the card's branch still
        # points at it.
        task = record.get("task") or ""
        branch = config.branch_for(task[:-3]) if task.endswith(".md") else ""
        now = resultidentity.git(config.app_repo(), "rev-parse", "--verify",
                   f"{branch}^{{commit}}") if branch else None
        if now is None or now.returncode != 0:
            moved("source-changed",
                  f"the branch this ran against ({branch or 'unknown'}) no "
                  f"longer exists, so what it covered cannot be checked")
        elif now.stdout.strip() != source.get("commit"):
            moved("source-changed",
                  f"the branch has moved on — it ran against "
                  f"{str(source.get('commit') or '')[:12]} and {branch} is "
                  f"now at {now.stdout.strip()[:12]}")
    elif not root.is_dir():
        moved("source-changed",
              f"the checkout it ran in ({root}) is gone, so what it ran "
              f"against can no longer be checked")
    else:
        now = resultidentity.source_identity(root)
        if now.get("sourceId") != source.get("sourceId"):
            if now.get("commit") != source.get("commit"):
                moved("source-changed",
                      f"the source has moved on — it ran against "
                      f"{str(source.get('commit') or '')[:12]} and the "
                      f"checkout is now at "
                      f"{str(now.get('commit') or '')[:12]}")
            else:
                moved("source-changed",
                      "tracked or untracked files in the checkout have "
                      "changed since it ran, so it did not run against this "
                      "code")
    if record.get("resultId"):
        # A record with a result identity is judged by what it depends on
        # and nothing else (design §4.1). The policy revision it was run
        # under is provenance: reported, never a reason.
        provenance = _identity_moved(record, moved, root)
        return {"fresh": not changed, "changed": changed, "reasons": reasons,
                "stale": bool(changed) and record.get("outcome") == "passed",
                "rules": "identity", "provenance": provenance}
    # — legacy: a record written before result identities existed. Read
    # with the rules it was written under, unchanged and never upgraded,
    # including `policy-changed` (design §14.3). —
    #
    # The program and the runner: what was invoked, and the code that read
    # its exit status. Both fail closed — a record that names no identity
    # cannot say the program is unchanged, so it is not reused as a pass.
    runner = (record.get("runner") or {}).get("revision")
    if not runner or runner != resultidentity.RUNNER_REVISION:
        moved("runner-changed",
              "bench's own runner has changed since this ran"
              if runner else
              "this was recorded by a runner that named no revision of "
              "itself, so it cannot be shown to be the runner here now")
    program = record.get("program")
    search = resultidentity.search_path()
    if not program or not program.get("bound"):
        moved("program-changed",
              (program or {}).get("why") if program and not program.get("bound")
              else "the record names no program identity, so the executable "
                   "it ran cannot be shown to be unchanged")
    elif resultidentity.program_identity(list(record.get("argv") or []), root,
                          search).get("programId") != program.get("programId"):
        moved("program-changed",
              "the check's executable, its interpreter or a file it was "
              "handed has changed since it ran — a program replaced at the "
              "same path is a different check")
    for step in record.get("prepare") or []:
        if step.get("outcome") != "prepared":
            continue
        frozen = step.get("program")
        try:
            argv = shlex.split(str(step.get("command") or ""))
        except ValueError:
            argv = []
        if not frozen or not frozen.get("bound") or \
                resultidentity.program_identity(argv, root, search).get("programId") \
                != frozen.get("programId"):
            moved("program-changed",
                  f"the preparation step '{step.get('name')}' runs a program "
                  f"that has changed, or was never identified, since it ran")
            break
    frozen_policy = record.get("policyRevision")
    frozen_context = record.get("contextIds")
    if frozen_policy or frozen_context:
        intent = record.get("intent") or "work"
        task = record.get("task") or ""
        try:
            # Asked the way the launch asked it: a review's policy was
            # resolved with the checkout it was pinned and no worktree, so
            # re-resolving it with the other roots would report bench's own
            # question as "the policy changed".
            current = profiles.resolve(
                intent,
                worktree=(config.WORKTREES / task[:-3])
                if task.endswith(".md") and intent != "review" else None,
                review=(where.get("path") if where.get("policy") == "review"
                        else None))
        except ValueError:
            current = None
        if (current is not None and frozen_policy
                and current["revision"] != frozen_policy):
            moved("policy-changed",
                  "the execution policy — which carries the check's own "
                  "definition — has changed since it ran")
        # The third binding, and the one that was named here before it was
        # compared: a context identity is `name@<content hash>`, so an
        # edited approved input is a different identity rather than the
        # same one with new bytes. A pass recorded under inputs the run
        # was *given* is not a pass under different ones — and the policy
        # revision cannot stand in for it, because that digests which
        # files were selected and not what is in them.
        if current is not None and frozen_context:
            now = {i.get("contextId") for i in current.get("context") or []
                   if i.get("contextId")}
            gone = [i for i in frozen_context if i not in now]
            if gone:
                moved("context-changed",
                      f"{len(gone)} approved input"
                      f"{'' if len(gone) == 1 else 's'} this ran with "
                      f"{'has' if len(gone) == 1 else 'have'} changed or gone "
                      f"since — it did not run with the inputs in force now")
    return {"fresh": not changed, "changed": changed, "reasons": reasons,
            "stale": bool(changed) and record.get("outcome") == "passed",
            "rules": "legacy"}


def _current_definition(record: dict) -> tuple[dict | None, list[dict],
                                                dict | None, str | None]:
    """The check as the policy declares it *now*, the asking intent's own
    preparation now, the resolution they came from, and why there is no
    answer when there is none.

    Read for identity, not for permission: a check a policy no longer
    offers to this intent is still the same definition if the document
    still declares it, and whether it may be *run* is `request`'s question,
    asked afresh every time (design §4.3)."""
    intent = record.get("intent") or "work"
    task = record.get("task") or ""
    where = record.get("root") or {}
    try:
        current = profiles.resolve(
            intent,
            worktree=(config.WORKTREES / task[:-3])
            if task.endswith(".md") and intent != "review" else None,
            review=(where.get("path") if where.get("policy") == "review"
                    else None))
    except ValueError as exc:
        return None, [], None, (f"the policy cannot be resolved now ({exc}), "
                                f"so the check's definition cannot be shown "
                                f"unchanged")
    name = record.get("check")
    check = next((c for c in current.get("verify") or []
                  if c.get("name") == name), None)
    if check is None:
        doc = profiles.document()
        steps, _ = profiles.document_steps(doc)
        declared, _ = profiles.document_checks(doc, steps)
        check = declared.get(name)
    if check is None:
        return None, [], current, (
            f"the check '{name}' is no longer declared by the policy as it "
            f"reads now, so what it would run cannot be shown to be what ran")
    return check, list(current.get("prepare") or []), current, None


def _env_now(record: dict) -> dict:
    """The environment a check like this one would be given now — built by
    the same function that built it, with placeholder paths, and nothing
    made on disk. Only names, and the values of declared variables, are
    ever compared."""
    where = Path((record.get("root") or {}).get("path") or ".")
    env, _ = resultidentity.child_env(where, Path("/nonexistent"), Path("/nonexistent"),
                       str(record.get("id") or ""), str(record.get("check") or ""))
    if record.get("inputs"):
        env["BENCH_VERIFY_INPUTS"] = "/nonexistent"
    # the directories a declaring check is handed, by name: demonstration
    # outputs (task 133) and the verifier report contract (task 135)
    if (record.get("outputs") or {}).get("declared"):
        env["BENCH_VERIFY_OUTPUTS"] = "/nonexistent"
    if (record.get("report") or {}).get("contract"):
        env["BENCH_VERIFY_REPORT_DIR"] = "/nonexistent"
        env["BENCH_VERIFY_REPORT"] = "/nonexistent"
    return env


def _identity_moved(record: dict, moved, root: Path) -> dict:
    """Every dimension of a record's result identity, compared with what
    it would be now — each that moved said by its own slug, naming the
    input, the variable, the step or the scope. Returns the provenance
    reading, which is reported and never a reason.

    Fails closed throughout: a dimension that cannot be re-read is a
    dimension that cannot be shown to hold."""
    identity = record.get("identity")
    if (not isinstance(identity, dict)
            or any(d not in identity
                   for d in resultidentity.IDENTITY_DIMENSIONS)
            or resultidentity.result_id(identity) != record.get("resultId")):
        moved("identity-unknown",
              "this record's result identity is incomplete or does not match "
              "its own digest, so it cannot be shown to hold — "
              + evidence.STALE_REMEDIES["identity-unknown"])
        return {"policyRevision": record.get("policyRevision"),
                "policyChanged": None}
    name = record.get("check")
    if identity.get("runner") != resultidentity.RUNNER_REVISION or \
            (record.get("runner") or {}).get("revision") != identity.get("runner"):
        moved("runner-changed", "bench's own runner has changed since this "
                                "ran — " + evidence.STALE_REMEDIES["runner-changed"])

    check, intent_prepare, current, why = _current_definition(record)
    frozen_policy = record.get("policyRevision")
    provenance = {
        "policyRevision": frozen_policy,
        "policyNow": (current or {}).get("revision"),
        "policyChanged": bool(current and frozen_policy
                              and current.get("revision") != frozen_policy),
        "contextIds": list(record.get("contextIds") or []),
    }
    if provenance["policyChanged"]:
        provenance["note"] = (
            "the execution policy has changed since this ran. That is "
            "provenance, not a reason: nothing this check declares it depends "
            "on moved with it, so the result still holds")

    if check is None:
        moved("check-changed", f"{why} — {evidence.STALE_REMEDIES['check-changed']}")
    else:
        if check.get("checkSubstance") != identity.get("check"):
            moved("check-changed",
                  f"'{name}''s command, root, time limit, declared inputs or "
                  f"declared environment changed since it ran — "
                  f"{evidence.STALE_REMEDIES['check-changed']}")
        scope = record.get("scope")
        if scope:
            args = (check.get("scopes") or {}).get(scope)
            if args is None:
                moved("scope-changed",
                      f"the scope '{scope}' is no longer declared on "
                      f"'{name}' — {evidence.STALE_REMEDIES['scope-changed']}")
            elif resultidentity.coverage_id(scope, list(args)) != identity.get("coverage"):
                moved("scope-changed",
                      f"the scope '{scope}' now runs different arguments — "
                      f"{evidence.STALE_REMEDIES['scope-changed']}")
        steps_now = [*intent_prepare, *(check.get("prepare") or [])]
        if resultidentity.prepare_definition(steps_now) != \
                (identity.get("prepare") or {}).get("definition"):
            was = [s.get("name") for s in record.get("prepareSteps") or []]
            now = [s.get("name") for s in steps_now]
            named = sorted(set(was) ^ set(now)) or now or was
            moved("prepare-changed",
                  f"the preparation {', '.join(repr(n) for n in named)} "
                  f"changed since it passed — what it runs, its network use "
                  f"or whether it is required — "
                  f"{evidence.STALE_REMEDIES['prepare-changed']}")

    search = resultidentity.search_path()
    frozen_program = identity.get("program")
    now_program = resultidentity.program_identity(list(record.get("argv") or []), root,
                                   search)
    if not frozen_program or \
            (record.get("program") or {}).get("programId") != frozen_program:
        moved("program-changed",
              "the record names no program identity, so the executable this "
              "check ran cannot be shown to be unchanged — "
              + evidence.STALE_REMEDIES["program-changed"])
    elif now_program.get("programId") != frozen_program:
        moved("program-changed",
              f"the check's executable {(record.get('argv') or ['?'])[0]}, "
              f"its interpreter or a file it was handed changed since it ran "
              f"— {evidence.STALE_REMEDIES['program-changed']}")
    frozen_steps = record.get("prepareSteps") or []
    frozen_programs = (identity.get("prepare") or {}).get("programs") or []
    for index, step in enumerate(frozen_steps):
        was = frozen_programs[index] if index < len(frozen_programs) else None
        argv = [step.get("executable"), *(step.get("argv") or [])]
        now = resultidentity.program_identity([a for a in argv if a], root, search)
        if not was or now.get("programId") != was:
            moved("program-changed",
                  f"the preparation step '{step.get('name')}' runs a program "
                  f"that has changed, or was never identified, since it ran — "
                  f"{evidence.STALE_REMEDIES['program-changed']}")
            break

    for item in record.get("inputs") or []:
        now = profiles.check_input(item)
        then = item.get("contextId")
        here = now.get("contextId") if now.get("status") == "resolved" else None
        if here == then:
            continue
        if then and not here:
            what = "is no longer there to be read"
        elif here and not then:
            what = "is there now, and was absent when it ran"
        else:
            what = "changed since it passed"
        moved("input-changed", f"the input '{item.get('name')}' {what} — "
                               f"{evidence.STALE_REMEDIES['input-changed']}")

    env_then = record.get("env") or {}
    declared = [d.get("name") for d in env_then.get("declared") or []]
    key = resultidentity.env_key(create=False) if declared else None
    env_now = _env_now(record)
    if declared and key is None:
        moved("env-changed",
              "the machine-local key that bound its declared environment "
              f"({', '.join(declared)}) is gone or unusable, so the values "
              "cannot be shown to be the same — "
              + evidence.STALE_REMEDIES["env-changed"])
    elif declared and resultidentity.key_id(key) != env_then.get("keyId"):
        moved("env-changed",
              "the machine-local key that bound its declared environment has "
              "been replaced since it ran, so the values cannot be compared — "
              + evidence.STALE_REMEDIES["env-changed"])
    else:
        bound = resultidentity.env_identity(env_now, declared, key)
        if bound["envId"] != identity.get("env"):
            added = sorted(set(bound["passed"]) - set(env_then.get("passed") or []))
            gone = sorted(set(env_then.get("passed") or []) - set(bound["passed"]))
            values = [b["name"] for b, a in zip(bound["declared"],
                                                env_then.get("declared") or [])
                      if b.get("hmac") != a.get("hmac")]
            if values:
                what = (f"the environment variable"
                        f"{'' if len(values) == 1 else 's'} "
                        f"{', '.join(values)} changed since it passed")
            elif added or gone:
                what = ("the set of environment variables a check is given "
                        "changed since it ran ("
                        + "; ".join(p for p in (
                            f"now also {', '.join(added)}" if added else "",
                            f"no longer {', '.join(gone)}" if gone else "")
                            if p) + ")")
            else:
                what = "the environment the check is given changed since it ran"
            moved("env-changed", f"{what} — {evidence.STALE_REMEDIES['env-changed']}")
    return provenance


def outcome_now(record: dict) -> dict:
    """The outcome a reader should be shown: what was recorded, unless its
    binding has gone, in which case `stale`.

    This is the one function that may disagree with the stored `outcome`,
    and it may only ever disagree in the safe direction: a `passed` whose
    source or policy moved becomes `stale`, and nothing ever becomes
    `passed` here.

    Memoised for `FRESH_TTL`, because the question costs git. Only a
    `passed` can change reading at all, so only a `passed` is asked —
    everything else is already the record's own last word, and no amount
    of re-reading turns a failure into anything else.
    """
    if record.get("outcome") != "passed":
        return {"outcome": record.get("outcome"), "reason": record.get("reason"),
                "why": record.get("why"),
                "freshness": {"fresh": True, "changed": [], "reasons": []},
                "recorded": record.get("outcome")}
    key = record.get("id") or ""
    cached = _FRESH.get(key)
    if cached and time.time() - cached[0] < FRESH_TTL:
        return cached[1]
    found = _outcome_now(record)
    if key:
        if len(_FRESH) > 500:        # a memo, not a store
            _FRESH.clear()
        _FRESH[key] = (time.time(), found)
    return found


_DRIFT: dict[str, tuple[float, dict]] = {}


def identity_drift(record: dict) -> dict | None:
    """Which dimensions of a record's *definition* — everything in its
    result identity but the source — have moved since it ran: the check,
    its coverage, programs, preparation, inputs, environment or runner.

    None for a legacy record, which has no identity to compare. The gate
    reads this to tell a record about this commit under the check as it is
    now (`current`) from one under a definition that has since changed
    (`outdated`, design §6.1 step 4), whatever its outcome — a failure
    under an old command is not today's failure. Memoised as `outcome_now`
    is, and for the same reason."""
    if not record or not record.get("resultId"):
        return None
    if record.get("outcome") == "passed":
        fresh = outcome_now(record)["freshness"]
    else:
        key = record.get("id") or ""
        cached = _DRIFT.get(key)
        if cached and time.time() - cached[0] < FRESH_TTL:
            fresh = cached[1]
        else:
            fresh = freshness(record)
            if key:
                if len(_DRIFT) > 500:
                    _DRIFT.clear()
                _DRIFT[key] = (time.time(), fresh)
    pairs = [(r, c) for r, c in zip(fresh.get("reasons") or [],
                                    fresh.get("changed") or [])
             if r not in evidence.SOURCE_REASONS]
    return {"reasons": [r for r, _ in pairs], "changed": [c for _, c in pairs]}


def _outcome_now(record: dict) -> dict:
    fresh = freshness(record)
    if fresh["stale"]:
        # The first finding's own slug, rather than a guess from its
        # wording: "the head moved" and "the policy moved" are different
        # things to do next, and a reader branching on the reason must
        # not have them collapse.
        return {"outcome": "stale",
                "reason": (fresh.get("reasons") or ["source-changed"])[0],
                "why": fresh["changed"][0], "freshness": fresh,
                "recorded": record.get("outcome")}
    return {"outcome": record.get("outcome"), "reason": record.get("reason"),
            "why": record.get("why"), "freshness": fresh,
            "recorded": record.get("outcome")}


def forget() -> None:
    """Drop this module's memos — the freshness and drift readings.
    `verify.forget_cached` calls it with every other owner's."""
    _FRESH.clear()
    _DRIFT.clear()
