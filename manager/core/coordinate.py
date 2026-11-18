"""Delivering a standalone card: evidence, independent review, repair and
the user's merge policy (task 142, phase 136).

The scheduler (task 141) launches implementation work. This is the half
that carries a card the rest of the way once its work is in `review/`:
ask bench for the candidate's required checks, ask for an independent
review of that exact head, launch a bounded repair when a check fails or
the review asks for changes, and — where the user delegated merging —
complete it under the merge rules the user configured. It owns no policy
and no second execution path: every step is an existing authority's own
door, asked through `orchestrator.authorize` immediately before it acts.

- **Checks** are `required.start`, the very batch **Run required checks**
  starts, under a person's work reading: only required checks, only the
  ones that reading may ask for, at the head the plan read.
- **Review** is `agents.start_pr_review`, a separate read-only run in a
  checkout pinned to the pull request's head.
- **Repair** is `agents.start_pr_fix` (↻ act on PR), counted against the
  scope's `repairRounds` ceiling.
- **Merge** is `completion.complete_task` with the exact head pinned, or
  `completion.complete_delivered` when the remote already holds it.

**One head decides (acceptance 1).** Every step is read against the
candidate commit `gates.candidate` names now, and every operation is
keyed by it. A commit that moves makes the old checks history (the gate's
relevance rule), the old verdict another commit's (`gates.verdict_at`),
and the old operations about something else; nothing selected for the
earlier head is carried over.

**Independent means a different model, shown to be one (D3, acceptance
2).** The reviewer's resolved identity — its harness and its model name,
mapped through what that harness declares in `capabilities.json` — is
compared with every implementer's: each run on this card that rode
`work` or `act-pr` (read from its frozen manifest) and the models those
two intents resolve to now, since a repair rides `act-pr`. The same model
under an alias, a harness default (no model passed, so bench does not
know which one ran), a name the harness does not declare and a reviewer
harness that is not installed or cannot review are each refused with a
remedy; nothing falls back to a weaker review. A verdict is counted only
when the run that gave it is shown independent the same way, from its own
manifest. A different model proves nothing about the evidence: the
review is also a separate run in its own pinned checkout, and checks are
bench's records at the head, never a report's sentence.

**Merge policy is the user's (D4, acceptance 3).** A delegated merge
waits for exactly the requirements already configured — the
`orchestrator.authorize` reading of `gates.evaluate` — and nothing else.
An ungated rule merges labelled *ungated — not verified*; no check is
required, none is called passed, no gate is activated and no override is
granted. Optional checks are never run here.

**Distinct endings, on the card (acceptance 4).** A repair ceiling
reached, a check the work reading may not ask for, a check that could not
reach a verdict (preparation failed, refused, no verdict), and authority
withdrawn between the decision and the act each end under their own code,
and each is written on the card (`decisions.note`): a blocker, an attempt
or an action receipt.

**Uncertain merges reconcile (acceptance 5).** A merge's intent is
written before it. A pass that finds an intent with no receipt asks the
authoritative records — the delivered record, the completion's own
recovery record, then the application remote itself — and a head the
remote already holds is finished by cleanup alone, never by a second
merge. What cannot be established is held as `unknown` for a person.
Nothing here releases, deploys or promotes anything.

Reading (`plan`) is a read: it asks local records and local git only, and
never the remote, a model or a check runner.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import agents
import agreements
import changes
import completion
import config
import decisions
import gates
import operations
import orchestrator
import plans
import profiles
import required
import state
import taskfiles

ACTIONS = ("verify", "review", "repair", "merge")
STAGE = "review"
# Which agent intents implement a card's code: the work run and ↻ act on
# PR, which is what a repair rides.
IMPLEMENTING = ("work", "act-pr")
# A required check that could not reach a verdict: a repair cannot fix a
# dependency that would not install or a request that was refused.
SETUP_REASONS = ("prepare-failed", "denied", "error")
NO_CHECKS = "No checks required by your policy"
# An authorization refusal that still leaves the card the scope's: the
# word for this particular step is not delegated. Every other refusal
# (ownership, pause, exclusion, the last mover...) means hands off.
CARD_LEVEL_OK = ("not-authorized",)

# Every code an item can carry, with the state it puts the card in. The
# words are what the page and the card's record say.
CODES = {
    "running": "working",
    "unmanaged": "unmanaged",
    "not-ours": "unmanaged",
    "in-flight": "held",
    "outcome-unknown": "held",
    "checks-running": "waiting",
    "requirements-pending": "waiting",
    "verify": "ready",
    "review": "ready",
    "repair": "ready",
    "merge": "ready",
    "merge-not-delegated": "awaiting",
    "no-candidate": "blocked",
    "no-pr": "blocked",
    "rules-invalid": "blocked",
    "dirty": "blocked",
    "check-not-granted": "blocked",
    "profile-blocked": "blocked",
    "verification-setup-failed": "blocked",
    "checks-unanswered": "blocked",
    "check-unmet": "blocked",
    "review-not-delegated": "blocked",
    "review-not-independent": "blocked",
    "review-ended-without-verdict": "blocked",
    "repair-not-delegated": "blocked",
    "repair-exhausted": "blocked",
    "repair-no-change": "blocked",
    "requirements-unmet": "blocked",
    "merge-refused": "blocked",
    "not-published": "blocked",
    "partial": "blocked",
}

# Independence codes (D3): only `independent` lets a review launch, and
# only a verdict whose run reads `independent` counts.
INDEPENDENCE = {
    "independent": "the reviewer's model is shown to differ from every "
                   "implementer's",
    "same-model": "the reviewer would ride the same model as an implementer",
    "reviewer-model-unresolved": "the review intent passes no model, so the "
                                 "harness picks one and bench cannot say which",
    "implementer-model-unknown": "an implementer rode the harness default, so "
                                 "bench cannot say which model wrote the code",
    "model-identity-unverified": "a model name is not one its harness declares, "
                                 "so bench cannot show it is or is not the same "
                                 "model",
    "review-unavailable": "the review harness is not installed or does not run "
                          "reviews",
    "verdict-unattributed": "the verdict names no run whose model bench "
                            "recorded",
}
REMEDY = {
    "same-model": "set BOARD_AGENT_MODEL_REVIEW in manager/local/.env (Settings "
                  "→ Agents) to a model other than the implementation and PR-fix "
                  "models",
    "reviewer-model-unresolved": "name the review model explicitly "
                                 "(BOARD_AGENT_MODEL_REVIEW, Settings → Agents)",
    "implementer-model-unknown": "name the implementation and PR-fix models "
                                 "explicitly (BOARD_AGENT_MODEL_WORK and "
                                 "BOARD_AGENT_MODEL_ACT_PR); a run that already "
                                 "rode the default stays unknown, so review it "
                                 "yourself",
    "model-identity-unverified": "use a model name the harness declares in its "
                                 "capabilities.json `models`, or declare it in a "
                                 "local adapter override",
    "review-unavailable": "choose an installed harness that runs reviews for "
                          "the review purpose (Settings → Agents)",
    "verdict-unattributed": "the orchestrator asks for its own review",
}


# ── who would review, and who wrote the code (D3) ─────────────────────


def _models_of(harness: str, cache: dict) -> dict | None:
    if harness not in cache:
        found = profiles.adapter_identity(harness)
        cache[harness] = found
    return cache[harness].get("models")


def identity(harness: str | None, model: str | None, *, source: str,
             cache: dict | None = None) -> dict:
    """One resolved model identity: the harness, the name passed, and the
    canonical model that name is, by the harness's own declaration —
    None when the harness declares no such name, or no name was passed."""
    cache = {} if cache is None else cache
    name = (model or "").strip().lower()
    out = {"harness": harness or None, "model": name or None,
           "canonical": None, "source": source}
    if not name or not harness:
        return out
    table = _models_of(harness, cache)
    if table:
        if name in table["canonical"]:
            out["canonical"] = name
        elif name in table["aliases"]:
            out["canonical"] = table["aliases"][name]
    return out


def _selection_identity(selection: dict | None, source: str,
                        cache: dict) -> dict:
    selection = selection or {}
    return identity((selection.get("harness") or {}).get("name"),
                    (selection.get("model") or {}).get("value"),
                    source=source, cache=cache)


def _card_manifests(filename: str, intents: tuple[str, ...]) -> list[dict]:
    """Every frozen manifest of a run on this card riding one of
    `intents`, oldest first."""
    folder = profiles.manifest_path("x").parent
    found = []
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("task") == filename \
                and data.get("intent") in intents:
            found.append(data)
    found.sort(key=lambda m: m.get("recorded") or 0)
    return found


def implementers(filename: str, cache: dict | None = None) -> list[dict]:
    """Every model that wrote, or would next write, this card's code: the
    runs on record that rode `work` or `act-pr`, and what those intents
    resolve to now (a repair is an `act-pr` run)."""
    cache = {} if cache is None else cache
    found = [_selection_identity(m.get("selection"),
                                 f"run {m.get('agent') or 'unknown'} "
                                 f"({m.get('intent')})", cache)
             for m in _card_manifests(filename, IMPLEMENTING)]
    for intent in IMPLEMENTING:
        choice = config.agent_choice(intent)
        found.append(identity(choice["harness"]["value"],
                              choice["model"]["value"],
                              source=f"configured {intent}", cache=cache))
    return found


def _compare(reviewer: dict, others: list[dict]) -> dict:
    if not reviewer["model"]:
        return _verdict_word("reviewer-model-unresolved", reviewer, others)
    for other in others:
        if not other["model"]:
            return _verdict_word("implementer-model-unknown", reviewer, others,
                                 culprit=other)
        if reviewer["model"] == other["model"] or (
                reviewer["canonical"] and
                reviewer["canonical"] == other["canonical"]):
            return _verdict_word("same-model", reviewer, others, culprit=other)
        if not (reviewer["canonical"] and other["canonical"]):
            return _verdict_word("model-identity-unverified", reviewer, others,
                                 culprit=other if other["canonical"] is None
                                 else reviewer)
    return _verdict_word("independent", reviewer, others)


def _verdict_word(code: str, reviewer: dict, others: list[dict], *,
                  culprit: dict | None = None) -> dict:
    reason = INDEPENDENCE[code]
    if culprit is not None and code == "same-model":
        alias = reviewer["model"] != culprit["model"]
        reason = (f"the reviewer's {reviewer['model']} is "
                  + (f"the same model as {culprit['source']}'s "
                     f"{culprit['model']} ({reviewer['canonical']})"
                     if alias else f"what {culprit['source']} rides"))
    elif culprit is not None and code == "implementer-model-unknown":
        reason = f"{culprit['source']} rode the harness default — {reason}"
    elif culprit is not None and code == "model-identity-unverified":
        reason = (f"'{culprit['model']}' ({culprit['source']}) is not a model "
                  f"its harness '{culprit['harness']}' declares — {reason}")
    return {"independent": code == "independent", "code": code,
            "reason": reason, "remedy": REMEDY.get(code),
            "reviewer": reviewer, "implementers": others}


def independence(filename: str, cache: dict | None = None) -> dict:
    """Would a review launched now be independent of this card's
    implementers? Refused, never weakened, when it cannot be shown."""
    cache = {} if cache is None else cache
    choice = config.agent_choice("review")
    harness = choice["harness"]["value"]
    if harness not in cache:
        cache[harness] = profiles.adapter_identity(harness)
    adapter = cache[harness]
    reviewer = identity(harness, choice["model"]["value"],
                        source="configured review", cache=cache)
    others = implementers(filename, cache)
    if adapter.get("path") is None or \
            not profiles.intent_supported(adapter, "review"):
        return _verdict_word("review-unavailable", reviewer, others)
    return _compare(reviewer, others)


def verdict_independence(verdict: dict, filename: str,
                         cache: dict | None = None) -> dict:
    """Was the run that gave this verdict independent of the card's
    implementers, read from its own frozen manifest?"""
    cache = {} if cache is None else cache
    others = implementers(filename, cache)
    frozen = profiles.recorded(verdict.get("agent") or "") \
        if verdict.get("agent") else None
    if not frozen or frozen.get("intent") != "review":
        return _verdict_word("verdict-unattributed",
                             identity(None, None, source="the verdict"), others)
    reviewer = _selection_identity(frozen.get("selection"),
                                   f"review run {verdict.get('agent')}", cache)
    return _compare(reviewer, others)


# ── reading one card ──────────────────────────────────────────────────


def _number(task: dict) -> str | None:
    return taskfiles.canonical_number(task["number"]) if task.get("number") else None


def _running(filename: str) -> bool:
    with state.LOCK:
        return any(r.get("task") == filename and r.get("status") == "running"
                   for r in state.AGENTS.values())


def _mine(filename: str, ops: list[dict]) -> list[dict]:
    return [o for o in ops if o.get("file") == filename
            and o.get("action") in ACTIONS]


def _same(ops: list[dict], action: str, head: str) -> list[dict]:
    return [o for o in ops if o.get("action") == action
            and o.get("basis") == head]


def rounds(filename: str, ops: list[dict] | None = None) -> int:
    """Repair rounds this card has spent: every repair that may have run,
    whatever head it was about. A restart, a pause or a new head resets
    nothing."""
    ops = operations.all_ops() if ops is None else ops
    return len([o for o in ops if o.get("file") == filename
                and o.get("action") == "repair"
                and o["status"] in operations.CONSUMING])


def _set(item: dict, code: str, reason: str, **extra) -> dict:
    item.update(state=CODES[code], code=code, reason=reason, **extra)
    if CODES[code] == "ready":
        item["action"] = code
    return item


def assess(task: dict, reading: dict, ops: list[dict],
           cache: dict | None = None, board: dict | None = None) -> dict:
    """What the next delivery step for one review-stage card is, and why.
    A read: local records and local git only."""
    cache = {} if cache is None else cache
    filename = task["file"]
    item = {"card": filename, "number": _number(task), "stage": task["stage"]}
    sid, _why = orchestrator.scope_of(task, reading)
    item["scope"] = sid
    if sid != agreements.BENCH or task.get("isPhase"):
        return _set(item, "unmanaged", "a phase's cards are integrated by "
                    "their phase scope; this pass delivers standalone cards")
    # whether this card is the scope's to act on at all — ownership, the
    # agreement, pause, exclusion, hand-back, the last mover (D1),
    # adoption — before anything about it is read or written. Which
    # action is delegated is asked again for the step itself.
    gatekeeper = orchestrator.authorize("verify", filename, board=board)
    if not gatekeeper["allowed"] and gatekeeper["code"] not in CARD_LEVEL_OK:
        return _set(item, "not-ours", gatekeeper["reason"],
                    authority=gatekeeper["code"])
    if _running(filename):
        return _set(item, "running", "a run is working on it")
    here = agreements.scope(reading, sid)
    scope = gates.scope_for(task)
    found = gates.candidate(filename, scope, task=task)
    head = found.get("commit") if found.get("state") == "present" else None
    if not head:
        return _set(item, "no-candidate", "there is no implementation commit "
                    "on this board to deliver")
    item["head"] = head
    mine = _mine(filename, ops)
    for op in mine:
        if op["status"] == "unknown":
            return _set(item, "outcome-unknown", f"operation {op['key']} "
                        f"({op['action']}) has an unknown outcome; it is held "
                        f"until a person says what happened", operation=op["key"])
        if op["status"] in ("intent", "launched", "unobserved"):
            code = "checks-running" if op["action"] == "verify" else "in-flight"
            return _set(item, code, f"operation {op['key']} ({op['action']}) "
                        f"is {op['status']}", operation=op["key"])
    gate = gates.evaluate(filename, scope, task=task)
    item["gate"] = {"decision": gate.get("decision"), "scope": scope,
                    "summary": gate.get("summary")}
    if gate.get("decision") == "blocked" and any(
            r.get("reason") == "policy-refused" for r in gate.get("unmet") or []):
        return _set(item, "rules-invalid", gate.get("summary") or
                    "the merge rules do not resolve")

    # 1. the required checks, at this head
    rows = [r for r in gate.get("requirements") or [] if r.get("kind") == "check"]
    item["checks"] = {"required": len(rows),
                      "met": len([r for r in rows if r["state"] == "met"]),
                      "words": (NO_CHECKS if gate.get("decision") == "ungated"
                                or not rows else
                                f"{len([r for r in rows if r['state'] == 'met'])}"
                                f" of {len(rows)} required checks met at "
                                f"{head[:12]}")}
    if any(r["state"] == "pending" for r in rows):
        return _set(item, "checks-running", "a required check is running at "
                    "this head; nothing is decided from its absence")
    unmet = [r for r in rows if r["state"] == "unmet"]
    setup = [r for r in unmet if r.get("reason") in SETUP_REASONS]
    if setup:
        first = setup[0]
        return _set(item, "verification-setup-failed",
                    f"{first.get('name')} could not reach a verdict at "
                    f"{head[:12]} ({first.get('reason')}): "
                    f"{first.get('why') or gates.plain(first.get('reason'), head)}"
                    " — a repair cannot fix the check's own setup",
                    evidence=[r.get("evidence") for r in setup if r.get("evidence")])
    failed = [r for r in unmet if r.get("reason") == "failed"]
    needed = [r for r in unmet if r not in failed
              and gates.severity(r.get("reason") or "") == "needed"]
    other = [r for r in unmet if r not in failed and r not in needed]
    if other:
        return _set(item, "check-unmet", f"{other[0].get('name')}: "
                    f"{other[0].get('why') or other[0].get('reason')}")
    if needed and not failed:
        return _checks_needed(item, filename, head, mine)
    if failed:
        first = failed[0]
        return _repair(item, task, here, head, mine, ops, "check-failed",
                       f"{first.get('name')} failed at {head[:12]}",
                       evidence=[r.get("evidence") for r in failed
                                 if r.get("evidence")])

    # 2. the independent review, at this head
    review_granted = here["authority"]["review"]["allowed"]
    item["review"] = {"delegated": review_granted}
    verdict = gates.verdict_at(filename, head)
    reviewed = False
    if verdict:
        counted = verdict_independence(verdict, filename, cache)
        # whether the verdict on record counts: its own run's model, read
        # apart from whether a review launched now would be independent
        item["review"].update(verdict=verdict.get("verdict"),
                              counted=_public_independence(counted))
        if verdict.get("verdict") == "REQUEST CHANGES":
            return _repair(item, task, here, head, mine, ops, "changes-requested",
                           f"the review at {head[:12]} asked for changes")
        reviewed = counted["independent"]
    if not reviewed:
        if not review_granted:
            if "bench" in (gate.get("reviews") or []) and not verdict:
                return _set(item, "review-not-delegated", "your merge rule "
                            "requires a Bench review and the agreement does not "
                            "delegate reviewing: ◔ review PR is yours")
        else:
            if not task.get("pr"):
                return _set(item, "no-pr", "the card has no pull request to "
                            "review — ↑ open PR first")
            if any(o["status"] == "ended" for o in _same(mine, "review", head)):
                return _set(item, "review-ended-without-verdict", "a review of "
                            f"{head[:12]} ended without a verdict that counts; "
                            "reviewing it again is a person's decision")
            ready = independence(filename, cache)
            item["review"]["independence"] = _public_independence(ready)
            if not ready["independent"]:
                return _set(item, "review-not-independent",
                            f"{ready['reason']} — {ready['remedy']}",
                            independence=ready["code"])
            return _set(item, "review", f"an independent review of {head[:12]} "
                        f"({ready['reviewer']['model']}, a different model from "
                        f"the implementers)")

    # 3. the merge, under the rules the user configured
    decision = gate.get("decision")
    if decision == "pending":
        return _set(item, "requirements-pending", gate.get("summary") or "")
    if decision == "blocked":
        return _set(item, "requirements-unmet", f"{gate.get('summary')} — "
                    f"delegation never bypasses a requirement you configured")
    label = {"ungated": "ungated — nothing is required by your policy, so "
                        "this merge is not verified",
             "allowed": "every requirement you configured is met",
             "overridden": "you overrode unmet requirements; not a pass",
             }.get(decision, decision)
    item["label"] = label
    for op in _same(mine, "merge", head):
        if op["status"] == "refused":
            return _set(item, "merge-refused", f"the merge was refused: "
                        f"{op.get('reason')}", operation=op["key"])
        if op["status"] == "ended" and op.get("outcome") in (
                "not-published", "partial"):
            return _set(item, op["outcome"], op.get("observed") or
                        "the merge did not deliver this head", operation=op["key"])
    if not here["authority"]["merge"]["allowed"]:
        return _set(item, "merge-not-delegated", f"ready to merge ({label}); "
                    f"merging is yours — the agreement does not delegate it")
    return _set(item, "merge", f"merge {head[:12]} — {label}")


def _public_independence(found: dict) -> dict:
    return {"independent": found["independent"], "code": found["code"],
            "reason": found["reason"], "remedy": found["remedy"],
            "reviewer": {k: found["reviewer"].get(k) for k in
                         ("harness", "model", "canonical", "source")},
            "implementers": [{k: o.get(k) for k in
                              ("harness", "model", "canonical", "source")}
                             for o in found["implementers"]]}


def _checks_needed(item: dict, filename: str, head: str,
                   mine: list[dict]) -> dict:
    reading = required.plan(filename, STAGE)
    if reading.get("head") and reading["head"] != head:
        return _set(item, "in-flight", "the candidate moved while it was read; "
                    "the next pass reads it again")
    unavailable = [i for i in reading.get("items") or []
                   if i.get("state") == "unavailable"]
    if unavailable:
        first = unavailable[0]
        return _set(item, "check-not-granted",
                    f"{first.get('check')} is required and a person's work "
                    f"reading may not ask for it: {first.get('remedy') or ''}"
                    .strip())
    refusal = reading.get("refusal")
    if refusal == "dirty":
        return _set(item, "dirty", reading.get("why") or required.REFUSALS["dirty"])
    if refusal == "profile-blocked":
        return _set(item, "profile-blocked", reading.get("why") or "")
    if any(o["status"] == "ended" for o in _same(mine, "verify", head)):
        return _set(item, "checks-unanswered", f"the checks requested at "
                    f"{head[:12]} ended without answering every requirement; "
                    f"Run required checks again is a person's decision")
    if not reading.get("requestable"):
        return _set(item, "checks-unanswered", reading.get("why") or
                    "no required check could be asked for")
    return _set(item, "verify", f"ask bench for {reading['requestable']} "
                f"required check(s) at {head[:12]}")


def _repair(item: dict, task: dict, here: dict, head: str, mine: list[dict],
            ops: list[dict], why: str, reason: str, **extra) -> dict:
    item["finding"] = why
    if not here["authority"]["repair"]["allowed"]:
        return _set(item, "repair-not-delegated", f"{reason}; the agreement "
                    f"does not delegate repair, so ↻ act on PR is yours", **extra)
    ceiling = here["ceilings"]["repairRounds"]["value"]
    spent = rounds(task["file"], ops)
    item["repair"] = {"rounds": spent, "ceiling": ceiling}
    if any(o["status"] == "ended" for o in _same(mine, "repair", head)):
        return _set(item, "repair-no-change", f"{reason}; a repair of "
                    f"{head[:12]} ended without a new commit", **extra)
    if spent >= ceiling:
        return _set(item, "repair-exhausted", f"{reason}; {spent} of {ceiling} "
                    f"repair rounds are spent — the next step is yours", **extra)
    if not task.get("pr"):
        return _set(item, "no-pr", f"{reason}; there is no pull request to act "
                    f"on", **extra)
    return _set(item, "repair", f"{reason}; repair round {spent + 1} of "
                f"{ceiling}", **extra)


def plan(board: dict | None = None) -> dict:
    """Every review-stage card the Bench scope manages, with its next
    delivery step. A read: it reserves, launches and writes nothing, and
    asks no remote."""
    board = taskfiles.collect() if board is None else board
    reading = agreements.effective()
    if not (reading["configured"] and reading["valid"]):
        return {"status": "off" if not reading["configured"] else "invalid",
                "items": []}
    ops = operations.all_ops()
    cache: dict = {}
    items = []
    for stage in board["stages"]:
        for task in stage["tasks"]:
            if task["stage"] != STAGE:
                continue
            try:
                items.append(assess(task, reading, ops, cache, board))
            except (OSError, ValueError) as exc:
                items.append({"card": task["file"], "number": _number(task),
                              "stage": task["stage"], "state": "blocked",
                              "code": "unreadable",
                              "reason": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return {"status": "on", "items": items}


# ── the card's record (D11) ───────────────────────────────────────────


def _note(filename: str, kind: str, fields: dict, key: str) -> None:
    stage = taskfiles.find_stage_of(filename)
    if not stage:
        return
    try:
        decisions.note(filename, stage, kind, fields, key=key[:80])
    except (ValueError, OSError):
        pass


def _blocker_id(filename: str, code: str) -> str:
    raw = "\x00".join(("coordinate", filename, code))
    return "b-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


_EVIDENCE_RE = re.compile(r"^evidence:[A-Za-z0-9._@:+-]{1,120}$")


def _evidence_refs(values) -> list[str]:
    refs = [f"evidence:{v}" for v in values or [] if isinstance(v, str)]
    return [r for r in refs if _EVIDENCE_RE.match(r)][:8]


def record_blockers(items: list[dict], board: dict) -> None:
    """Write each blocked card's blocker on the card once per head, and
    clear the delivery blockers a card no longer has."""
    texts = {t["file"]: t.get("body") for s in board["stages"] for t in s["tasks"]}
    for item in items:
        filename, head = item["card"], (item.get("head") or "nohead")[:12]
        open_now = set()
        body = texts.get(filename)
        if body is None:
            try:
                body = (config.TASKS / item["stage"] / filename).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                body = ""
        try:
            open_now = {b["blocker"] for b in decisions.read_text(body, filename)
                        ["blockers"] if b.get("state") == "open"
                        and b.get("operation") == "coordinate"}
        except (ValueError, KeyError, TypeError):
            open_now = set()
        mine = _blocker_id(filename, item.get("code") or "")
        if item.get("state") == "blocked":
            _note(filename, "blocker", {
                "blocker": mine, "state": "open", "operation": "coordinate",
                "text": f"{item['code']}: {item.get('reason') or ''}"[:1800],
                "evidence": _evidence_refs(item.get("evidence"))},
                f"coord-{item['code']}-{head}-open")
        for other in open_now - ({mine} if item.get("state") == "blocked" else set()):
            _note(filename, "blocker", {
                "blocker": other, "state": "cleared", "operation": "coordinate",
                "text": f"cleared at {head}: the card moved on"},
                f"coord-clear-{other}-{head}")
        if item.get("code") == "repair-exhausted":
            ceiling = (item.get("repair") or {}).get("ceiling") or 0
            _note(filename, "attempt", {
                "incident": f"repair-{head}", "attempt": min(ceiling, 99),
                "of": min(ceiling, 99), "outcome": "exhausted",
                "summary": item.get("reason") or "", "operation": "coordinate"},
                f"coord-exhausted-{head}")


def _receipt(op: dict, outcome: str, summary: str) -> None:
    _note(op["file"], "action", {
        "action": op["action"], "operation": op["key"], "phase": "receipt",
        "outcome": outcome, "summary": summary[:400]},
        f"{op['key']}-receipt-{outcome}")


# ── execute ───────────────────────────────────────────────────────────


def _begin(item: dict, answer: dict, task: dict) -> dict:
    action, head = item["action"], item["head"]
    prior = _same(operations.for_card(task["file"]), action, head)
    attempt = len(prior) + 1
    key = operations.key_for(action, task["file"], head, attempt)
    op = operations.begin(key, action=action, card=task["file"],
                          number=_number(task), scope=answer["scope"],
                          pool=orchestrator.LAUNCHES.get(action, "none"),
                          basis=head, attempt=attempt,
                          note="; ".join(answer.get("provenance") or []))
    _note(task["file"], "action", {
        "action": action, "operation": key, "phase": "intent",
        "outcome": "intended", "summary": item.get("reason", "")[:400]},
        f"{key}-intent")
    return op


def _refused(op: dict, code: str, reason: str) -> dict:
    done = operations.update(op["key"], "refused", code=code, reason=reason)
    _receipt(done, code if code in ("authority-revoked",) else "refused",
             f"not done: {reason}")
    return {"card": op["file"], "action": op["action"], "outcome": "refused",
            "code": code, "key": op["key"], "reason": reason}


def _revoked(op: dict, answer: dict) -> dict:
    """Authority withdrawn between the decision and the act — a pause, an
    exclusion, a hand-back, another person's move or an edited agreement:
    a refusal of its own, so it reads apart from a preflight's."""
    return _refused(op, "authority-revoked",
                    f"{answer['code']}: {answer['reason']}")


def execute(item: dict, task: dict, board: dict | None = None) -> dict:
    """One delivery step, with its intent before and its receipt after,
    authorized at the decision and again immediately before the act."""
    action, head, filename = item["action"], item["head"], task["file"]
    answer = orchestrator.authorize(action, filename, board=board)
    if not answer["allowed"]:
        return {"card": filename, "action": action, "outcome": "not-authorized",
                "code": answer["code"], "reason": answer["reason"]}
    try:
        op = _begin(item, answer, task)
    except operations.Exists as exists:
        return {"card": filename, "action": action, "outcome": "duplicate",
                "key": exists.op["key"]}
    again = orchestrator.authorize(action, filename, operation=op["key"])
    if not again["allowed"]:
        return _revoked(op, again)
    try:
        if action == "verify":
            return _verify(op, filename, head)
        if action == "review":
            return _review(op, filename, head)
        if action == "repair":
            return _repair_now(op, filename, head, task)
        return _merge(op, filename, head, task, again)
    except (ValueError, required.Refused) as exc:
        return _refused(op, getattr(exc, "code", None) or f"{action}-refused",
                        str(exc) or getattr(exc, "why", "") or action)
    except Exception as exc:  # noqa: BLE001 — anything unexplained is unknown
        done = operations.update(op["key"], "unknown", code=f"{action}-error",
                                 reason=f"{type(exc).__name__}: {exc}",
                                 observed="the step raised; whether it took "
                                          "effect is not known")
        _receipt(done, "unknown", "the step's outcome is unknown; held")
        return {"card": filename, "action": action, "outcome": "unknown",
                "key": op["key"]}


def _verify(op: dict, filename: str, head: str) -> dict:
    batch = required.start(filename, STAGE, head=head, actor="orchestrator",
                           trigger="orchestrator")
    done = operations.update(op["key"], "launched", batch=batch.get("id"),
                             observed="the required checks were requested")
    _receipt(done, "launched", f"required checks requested at {head[:12]}")
    return {"card": filename, "action": "verify", "outcome": "launched",
            "key": op["key"], "batch": batch.get("id")}


def _agent_receipt(op: dict, record: dict, words: str) -> dict:
    agent_id = (record or {}).get("id")
    with state.LOCK:
        proc = (state.AGENTS.get(agent_id or "") or {}).get("proc")
    done = operations.update(op["key"], "launched", agent=agent_id,
                             pid=getattr(proc, "pid", None),
                             observed="the launch returned its run")
    _receipt(done, "launched", f"{words} ({(record or {}).get('name') or agent_id})")
    return {"card": op["file"], "action": op["action"], "outcome": "launched",
            "key": op["key"], "agent": agent_id}


def _review(op: dict, filename: str, head: str) -> dict:
    # independence is read again immediately before the launch: a settings
    # change between the plan and the act must not slip a same-model review
    ready = independence(filename)
    if not ready["independent"]:
        return _refused(op, "review-not-independent",
                        f"{ready['reason']} — {ready['remedy']}")
    record = agents.start_pr_review(filename, STAGE)
    return _agent_receipt(op, record, f"independent review of {head[:12]} "
                          f"started")


def _repair_now(op: dict, filename: str, head: str, task: dict) -> dict:
    reading = agreements.effective()
    here = agreements.scope(reading, op["scope"]) or {}
    ceiling = ((here.get("ceilings") or {}).get("repairRounds") or {}).get("value", 0)
    spent = rounds(filename, [o for o in operations.all_ops()
                              if o["key"] != op["key"]])
    if spent >= ceiling:
        return _refused(op, "repair-exhausted", f"{spent} of {ceiling} repair "
                        f"rounds are spent")
    record = agents.start_pr_fix(filename, STAGE)
    _note(filename, "attempt", {
        "incident": f"repair-{head[:12]}", "attempt": spent + 1, "of": ceiling,
        "outcome": "started", "operation": op["key"],
        "summary": f"repair round {spent + 1} of {ceiling} at {head[:12]}"},
        f"{op['key']}-attempt")
    return _agent_receipt(op, record, f"repair round {spent + 1} of {ceiling} "
                          f"started")


def _merge(op: dict, filename: str, head: str, task: dict,
           answer: dict) -> dict:
    rname = changes.remote()
    target = plans.pr_base(filename, task)
    if rname:
        held, _tip, _why = completion.remote_holds(rname, target, head)
        if held is True:
            result = completion.complete_delivered(filename, STAGE, head)
            return _merged(op, result, "reconciled",
                           f"{rname}/{target} already held {head[:12]}; "
                           f"cleaned up without merging again")
    result = completion.complete_task(filename, STAGE, expect_head=head,
                                      actor="orchestrator")
    if result.get("moved"):
        return _merged(op, result, "merged", f"merged {head[:12]} into "
                       f"{target} — {answer.get('label') or ''}".strip(" —"))
    if result.get("partial"):
        return _merged(op, result, "partial", "merged the gated commit; the "
                       "branch has moved on, so the card stays in review")
    if result.get("merged") and not result.get("published"):
        return _merged(op, result, "not-published", "merged and not seen on "
                       "the remote; the completion's recovery record finishes it")
    return _merged(op, result, "ended", "merge & clean up ended without a move")


def _merged(op: dict, result: dict, outcome: str, words: str) -> dict:
    done = operations.update(op["key"], "ended", outcome=outcome, observed=words)
    _receipt(done, outcome, words)
    return {"card": op["file"], "action": "merge", "outcome": outcome,
            "key": op["key"]}


# ── observe and reconcile ─────────────────────────────────────────────


def _alive(pid, agent_id: str) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return False
    return f"BOARD_AGENT_ID={agent_id}".encode() in environ.split(b"\x00")


def observe(ops: list[dict] | None = None) -> list[dict]:
    """Receipts for steps whose effect has ended: a batch of checks that
    finished, a review or a repair run that ended. Writes receipts only."""
    changed = []
    for op in operations.all_ops() if ops is None else ops:
        if op.get("action") not in ACTIONS or \
                op["status"] not in ("launched", "unobserved"):
            continue
        if op["action"] == "verify":
            batch = required.latest(op["file"])
            if batch and batch.get("id") == op.get("batch"):
                if batch.get("state") == "running":
                    continue
                outcome = "answered" if not batch.get("stop") else "stopped"
                done = operations.update(op["key"], "ended", outcome=outcome,
                                         observed=f"the batch ended "
                                         f"({batch.get('stop') or 'answered'})")
            else:
                done = operations.update(op["key"], "ended", outcome="unobserved",
                                         observed="the batch is not on record; "
                                                  "its ending was not seen")
            _receipt(done, done["outcome"], f"required checks: {done['observed']}")
            changed.append(done)
            continue
        with state.LOCK:
            record = dict(state.AGENTS.get(op.get("agent") or "", {}))
        if record and record.get("status") == "running":
            continue
        if record:
            outcome = record.get("status") or "ended"
            done = operations.update(op["key"], "ended", outcome=outcome,
                                     rc=record.get("rc"),
                                     observed="the run's ending was observed")
        elif _alive(op.get("pid"), op.get("agent") or ""):
            if op["status"] != "unobserved":
                changed.append(operations.update(
                    op["key"], "unobserved", alive=True,
                    observed="the board restarted while this run was in flight; "
                             "its process is still running"))
            continue
        else:
            done = operations.update(op["key"], "ended", outcome="unobserved",
                                     alive=False, observed="the run ended while "
                                     "the board was not watching")
        word = done["outcome"] if str(done["outcome"]).isalpha() else "ended"
        _receipt(done, word, f"the {op['action']} run ended: {done['outcome']}")
        changed.append(done)
    return changed


def _manifest_since(filename: str, intent: str, since: float) -> bool:
    return any((m.get("recorded") or 0) >= since
               for m in _card_manifests(filename, (intent,)))


def reconcile(ops: list[dict] | None = None) -> list[dict]:
    """Settle every delivery intent left without a receipt, from what is
    authoritative — never by repeating the step."""
    changed = []
    for op in operations.all_ops() if ops is None else ops:
        if op.get("action") not in ACTIONS or op["status"] != "intent":
            continue
        since = (op.get("intentEpoch") or 0) - 1
        handler = {"verify": _reconcile_verify, "review": _reconcile_run,
                   "repair": _reconcile_run, "merge": _reconcile_merge}
        done = handler[op["action"]](op, since)
        if done is not None:
            changed.append(done)
    return changed


def _reconcile_verify(op: dict, since: float) -> dict:
    batch = required.latest(op["file"])
    if batch and (batch.get("requested") or 0) >= since \
            and batch.get("head") == op.get("basis"):
        done = operations.update(op["key"], "launched", batch=batch.get("id"),
                                 observed="reconciled: the batch was found")
        _receipt(done, "launched", "reconciled: the checks were requested")
        return done
    # a batch is recorded before its first check runs, so no record means
    # nothing was asked for
    return operations.update(op["key"], "abandoned", code="no-batch",
                             reason="no batch was recorded after the intent: "
                                    "nothing was requested",
                             observed="reconciled: nothing ran")


def _reconcile_run(op: dict, since: float) -> dict:
    mode, intent, prefix = (("review", "review", "review-pr-")
                            if op["action"] == "review" else
                            ("work", "act-pr", "fix-pr-"))
    with state.LOCK:
        found = [dict(r, id=k) for k, r in state.AGENTS.items()
                 if r.get("task") == op["file"] and str(k).startswith(prefix)
                 and (r.get("started") or 0) >= since]
    if found:
        run = found[-1]
        done = operations.update(op["key"], "launched", agent=run["id"],
                                 pid=getattr(run.get("proc"), "pid", None),
                                 observed="reconciled: the run was found")
        _receipt(done, "launched", "reconciled: the run was found")
        return done
    if _manifest_since(op["file"], intent, since + 1):
        # the launch freezes its manifest before any process starts, so a
        # manifest with no run in sight may be a process nobody is watching
        done = operations.update(op["key"], "unknown", code="launch-unconfirmed",
                                 reason="a manifest was recorded after the "
                                        "intent but no run is tracked; held",
                                 observed="reconciled: outcome unknown")
        _receipt(done, "unknown", "the launch's outcome is unknown; held")
        return done
    return operations.update(op["key"], "abandoned", code="no-launch",
                             reason="no manifest was recorded after the intent, "
                                    "and a launch records one before any process "
                                    "starts: nothing ran",
                             observed="reconciled: nothing ran")


def _reconcile_merge(op: dict, since: float) -> dict:
    filename, head = op["file"], op.get("basis") or ""
    delivered = completion.delivered_record(filename) or {}
    if delivered.get("commit") == head or taskfiles.find_stage_of(filename) == "done":
        return _merged(op, {}, "merged", "reconciled: the delivered record "
                       f"names {head[:12]}")
    pending = completion.recovery(filename) or {}
    if pending.get("commit") == head:
        return _merged(op, {}, "not-published", "reconciled: the merge landed "
                       "and its publication is unfinished; the completion's "
                       "recovery record finishes it without merging again")
    stage = taskfiles.find_stage_of(filename) or STAGE
    try:
        task = changes.woven(filename, stage)
    except (OSError, ValueError):
        task = {}
    target = plans.pr_base(filename, task)
    rname = changes.remote()
    held = None
    why = "this application has no remote"
    if rname:
        held, _tip, why = completion.remote_holds(rname, target, head)
    if held is True:
        # landed, its reply lost: the next pass cleans up through
        # complete_delivered, which merges nothing
        return _merged(op, {}, "landed", f"reconciled: {rname}/{target} holds "
                       f"{head[:12]}; cleanup follows, with no second merge")
    if not config.app_merge_on_origin() and held is False \
            and not gates.contained(head, target):
        return operations.update(op["key"], "abandoned", code="no-merge",
                                 reason=f"neither {target} here nor "
                                        f"{rname}/{target} holds {head[:12]}: "
                                        "nothing was merged",
                                 observed="reconciled: nothing merged")
    done = operations.update(op["key"], "unknown", code="merge-unconfirmed",
                             reason=f"whether {head[:12]} was merged could not "
                                    f"be established ({why or 'not seen'}); held",
                             observed="reconciled: outcome unknown")
    _receipt(done, "unknown", "the merge's outcome is unknown; held until a "
             "person says what happened")
    return done


# ── the pass ──────────────────────────────────────────────────────────


def run(board: dict) -> list[dict]:
    """The delivery half of one scheduler pass: plan every review-stage
    card, record its blockers, and take each card's one next step. Called
    by `scheduler.run_pass` under its lock, after observe and reconcile."""
    found = plan(board)
    if found["status"] != "on":
        return []
    record_blockers(found["items"], board)
    tasks = {t["file"]: t for s in board["stages"] for t in s["tasks"]}
    results = []
    for item in found["items"]:
        if item.get("state") != "ready":
            continue
        results.append(execute(item, tasks[item["card"]], board))
    return results


def projection(board: dict | None = None) -> dict:
    """What `GET /api/orchestration` shows of delivery. A read."""
    found = plan(board)
    return {**found, "noChecks": NO_CHECKS,
            "independence": "a review counts only from a different model, "
                            "shown different by its harness's declared names"}
