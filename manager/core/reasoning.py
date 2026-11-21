"""Orchestrator reasoning: bounded coding-agent jobs with a public,
versioned response (task 139, D2).

Every semantic judgement an orchestrator makes — interpreting a card's
readiness, drafting the owner's questions, planning, analysing a stall
for recovery — is a **launched coding-agent job** through the adapter
machinery, never a bare model call hidden in a scheduler or a renderer.
This module owns that job's whole lifecycle and nothing else:

- **What the run is given.** One reasoning *need* about one card, and
  the context it is about, *delivered*: the card itself (its brief and
  its public decision history), its phase card, its dependency cards and
  a facts document bench writes from its own public readings (the
  card's scope and the authority the agreement gives it, its movement
  eligibility, the evidence bench recorded for it, the binding below and
  the response contract). Each is pinned through the existing
  approved-context machinery (`profiles.pin`) and granted by one exact
  read, so delivery is proved by the adapter's own grant rather than by
  the operator being able to read the file. An adapter that cannot
  deliver that way refuses the intent (`profiles.REASON_DELIVERY`).
- **What the run may do.** The `reason` intent: no worktree, no command,
  no shell, no edit tool — the refine stance exactly. It proposes; it
  never executes, edits the agreement, moves a card or authorizes
  anything. A later, expressly authorized repair run is a different
  intent (`work`/`act-pr`), launched through its own path.
- **What the run must answer.** One JSON object between
  `<<<REASONING` and `REASONING>>>` (`CONTRACT`): a public summary,
  findings with evidence references, proposed questions and actions,
  blockers, and the input binding it was given, echoed back. There is no
  field for private reasoning and an unknown key is refused, so a
  chain-of-thought dump is refused rather than kept.
- **What bench does with it.** Validates it — bounds, identity
  (operation, need, card), scope (an action may name only this card, or
  a member of this phase), evidence references (only inputs delivered to
  this run, cards in its context and evidence bench recorded for the
  card), and the input binding against the one the run was launched
  with *and* against the board now. A response that fails any of these
  is retained as a diagnostic outcome (`invalid`, `stale`), its raw text
  kept bounded and scrubbed, and never read as an instruction. A valid
  response is still only a proposal: `executable` is false on every
  action, and nothing here executes one.

**Triggered by a need, not by a read.** `request()` is the only thing
that launches, and nothing on a read path calls it: opening a card,
`/api/state`, `/api/orchestration` and `for_card` read records and
launch nothing. A request is keyed by a fingerprint of the need and the
input binding, so a repeated unchanged scheduler tick, a double click or
two concurrent callers get the one job back (`duplicate`); a changed
input is a new question.

**Usage is accounted.** An orchestrator's request is authorized by
`orchestrator.authorize("reason", …)` — slots, daily starts and
launches per card included — and counted in its launch ledger. Every
job records which pool it used, the harness and model it rode and what
usage the vendor reported (none, today: said as `not reported`, never
as zero).

Runtime state is machine-local (`state/reasoning/`), like refinement
jobs: job records, facts documents, retained raw responses. They are not
the record a person reads: when a job settles, its public outcome — the
validated response, or why it was refused or ended — goes onto its card
through `decisions.record_response` (task 140), and a valid response's
questions are opened there for the owner. Raw text never goes to a card.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path

import agents
import agreements
import config
import decisions
import evidence
import movements
import orchestrator
import profiles
import records
import redact
import state
import taskfiles
import templates

KIND = "bench-reasoning-response"
FACTS_KIND = "bench-reasoning-facts"
VERSION = 1

# The reasoning needs an orchestrator has (D2), each one question. What
# each need *asks* is operator-adaptable prose (task 153): the guidance
# template `templates.need_template(need)`, composed into the shared
# `reason.md` by `templates.compose_reasoning`. What is kept here is only
# what the need is, for a reader; the schema, bounds and authority checks
# below never come from a prompt.
NEEDS = {
    "readiness": "is this card ready to be worked on as written",
    "questions": "what must the product owner decide first",
    "plan": "how should this card's delivery be sequenced",
    "recovery": "what happened to a stalled or failed run, and what bounded "
                "recovery to propose",
    # A person's free-text question about a card, a phase or the board
    # (task 181). Only ever asked by a person; its answer goes into the
    # conversation it belongs to and explains — it proposes no action and
    # opens no question, and nothing executes from it.
    "ask": "a person's question about what the orchestration is doing, "
           "answered from bench's own records",
}
ASK_KEYS = {"answer", "gaps", "suggestions"}
MAX_ASK_ITEMS = {"gaps": 10, "suggestions": 8}
ASK_SUGGESTION_KEYS = ({"id", "suggestion", "by", "control", "card", "evidence"},
                       {"id", "suggestion", "by"})
MAX_ASK_HISTORY = 6
MAX_ASK_CARDS = 60

# queued → running → valid | invalid | stale
#                  ↘ failed | cancelled
STATUSES = ("queued", "running", "valid", "invalid", "stale", "failed",
            "cancelled")
ACTIVE = {"queued", "running"}
# endings a new request with the same fingerprint answers with, rather than
# launching again: the same question about the same inputs has an answer
SETTLED = {"valid", "invalid", "stale"}

# What a response may propose. The orchestrator's own action words, plus
# asking the owner and holding. A proposal is never a grant: each is
# authorized again, by `orchestrator.authorize`, when anything acts on it.
PROPOSABLE = tuple(orchestrator.ACTIONS) + ("ask-owner", "hold")
SEVERITIES = ("info", "warning", "blocker")

# Bounds, every one refused rather than clipped: a response over a bound
# is not a shorter response, it is a response the run did not keep to.
MAX_BLOCK = 64 * 1024
MAX_SUMMARY = 1200
MAX_TEXT = 800
MAX_ITEMS = {"findings": 20, "questions": 10, "actions": 10, "blockers": 10}
MAX_OPTIONS = 6
MAX_REFS = 8
MAX_DEPENDENCIES = 6
MAX_EVIDENCE_FACTS = 20
MAX_RAW = 16 * 1024
ITEM_ID = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
REF_RE = re.compile(r"^(input|card|evidence):[A-Za-z0-9._@:+-]{1,120}$")
# control characters other than newline and tab, and the bidi overrides
# that make a string read as something it is not
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]")

TOP_KEYS = {"v", "kind", "operation", "need", "card", "inputs", "summary",
            "findings", "questions", "actions", "blockers"}
ITEM_KEYS = {
    "findings": ({"id", "statement", "severity", "evidence"},
                 {"id", "statement"}),
    "questions": ({"id", "question", "options", "recommendation", "why",
                   "evidence"}, {"id", "question"}),
    "actions": ({"id", "action", "card", "why", "evidence"},
                {"id", "action", "card", "why"}),
    "blockers": ({"id", "blocker", "evidence"}, {"id", "blocker"}),
}

BLOCK_RE = re.compile(r"<<<REASONING[ \t]*\r?\n(?P<body>.*?)\r?\nREASONING>>>",
                      re.DOTALL)
DECLINED_RE = re.compile(r"^REASONING DECLINED:\s*(.*)$", re.MULTILINE)

LOCK = threading.RLock()
JOBS: dict[str, dict] = {}
_loaded = False


class Conflict(ValueError):
    """The request is valid; its last attempt already ended, and asking
    again is a deliberate retry."""


# ── persistence ────────────────────────────────────────────────────────


def _dir() -> Path:
    return config.REASONING_DIR


def _path(job_id: str) -> Path:
    return _dir() / "jobs" / f"{job_id}.json"


def _save(job: dict) -> None:
    records.write_json(_path(job["id"]), job)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    directory = _dir() / "jobs"
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        found = records.read_json(path)
        if isinstance(found, dict) and found.get("id"):
            JOBS.setdefault(found["id"], found)


def reset() -> None:
    """Forget memory so the next read is from disk — a restart, for tests."""
    global _loaded
    with LOCK:
        JOBS.clear()
        _loaded = False


def recover() -> int:
    """At board start: a job recorded as queued or running belongs to a
    process this board no longer has. It is closed as failed, naming the
    restart, and never re-run by itself — the scheduler asks again when
    the need is still there, under the same fingerprint, deliberately."""
    closed = 0
    asked = []
    with LOCK:
        _load()
        for job in JOBS.values():
            if job.get("status") in ACTIVE:
                job.update({"status": "failed", "ended": time.time(),
                            "error": "the board restarted while this reasoning "
                                     "run was in flight, so its response was "
                                     "lost; nothing was acted on"})
                _save(job)
                closed += 1
                if job.get("need") == "ask":
                    asked.append(dict(job))
    # a person's question says so in its conversation (task 181), so the
    # page offers Retry rather than waiting on a run that is gone
    for job in asked:
        on_card(job)
    return closed


# ── what a run is about: the card, its context, its binding ────────────


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tasks(board: dict | None) -> list[dict]:
    board = taskfiles.collect() if board is None else board
    return [t for stage in board["stages"] for t in stage["tasks"]]


def _number(task: dict | None) -> str | None:
    if not task or not task.get("number"):
        return None
    return taskfiles.canonical_number(task["number"])


def _card_file(task: dict) -> Path:
    return config.TASKS / task["stage"] / task["file"]


def context_cards(filename: str, tasks: list[dict]) -> dict:
    """The cards one reasoning run is about: the card, its phase (or, for
    a phase card, its members), and the cards it depends on — each read
    off the board as it is now."""
    by_file = {t["file"]: t for t in tasks}
    by_number = {_number(t): t for t in tasks if _number(t)}
    task = by_file.get(filename)
    if task is None:
        raise ValueError(f"{filename} is not on this board — refresh the board")
    phase = None
    if task.get("phase") and not task.get("isPhase"):
        phase = by_file.get(task["phase"]["file"])
    members = ([by_file[m["file"]] for m in task.get("members") or []
                if m["file"] in by_file] if task.get("isPhase") else [])
    deps = []
    for raw in task.get("dependsOn") or []:
        dep = by_number.get(taskfiles.canonical_number(str(raw)))
        if dep is not None and dep["file"] != filename and dep not in deps:
            deps.append(dep)
    return {"task": task, "phase": phase, "members": members,
            "dependencies": deps[:MAX_DEPENDENCIES]}


def binding(filename: str, board: dict | None = None) -> dict:
    """The revisions a response is bound to: the card's whole text (its
    brief and its decision history), its stage, its phase card and its
    dependency cards, and the operating agreement in force. Computed
    again when a response comes back and whenever one is read, so a
    response about a card that has since changed reads stale."""
    tasks = _tasks(board)
    cards = context_cards(filename, tasks)
    task = cards["task"]

    def rev(card: dict | None) -> str | None:
        # the card's text without the orchestration records bench itself
        # wrote, plus every decision a person made (task 140): putting this
        # response on the card does not make it stale; an answer does
        if card is None:
            return None
        try:
            return decisions.card_rev(_card_file(card).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return "unreadable"

    text = _card_file(task).read_text(encoding="utf-8")
    return {
        "card": _number(task) or task["file"],
        "file": task["file"],
        "stage": task["stage"],
        "cardRev": rev(task),
        "briefRev": taskfiles.brief_sha(taskfiles.card_parts(text)["brief"])[:16],
        "phaseRev": rev(cards["phase"]),
        "memberRevs": {(_number(m) or m["file"]): rev(m) for m in cards["members"]},
        "dependencyRevs": {(_number(d) or d["file"]): rev(d)
                           for d in cards["dependencies"]},
        "agreementRev": agreements.effective()["revision"],
    }


def board_binding(conversation: str, board: dict | None = None) -> dict:
    """What a board-wide question's answer is bound to (task 181): which
    cards stand in which working stage, the people's records in its
    conversation, and the agreement in force. A card moving, a new
    question in the conversation or an agreement edit makes an answer
    given before it stale."""
    tasks = _tasks(board)
    stages = sorted(f"{t['file']}:{t['stage']}" for t in tasks
                    if t["stage"] in orchestrator.MANAGED_STAGES)
    try:
        text = decisions.conversation_path(conversation).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    return {"card": "board", "conversation": conversation,
            "conversationRev": decisions.card_rev(text) if text else None,
            "boardRev": profiles.digest("board", *stages)[:16],
            "agreementRev": agreements.effective()["revision"]}


def _binding_for(job: dict, board: dict | None = None) -> dict:
    target = ((job.get("ask") or {}).get("target") or {})
    if target.get("kind") == "board":
        return board_binding(job["ask"]["conversation"], board)
    return binding(job["task"], board)


def fingerprint(need: str, bound: dict, subject: dict | None = None) -> str:
    """The question's identity: the need and the input binding — and, for
    a recovery diagnosis (task 144), the incident and attempt it serves,
    so a second attempt about an unchanged card is a new question while a
    repeated tick about the same attempt is the same one."""
    parts = ["reasoning", str(VERSION), need, json.dumps(bound, sort_keys=True)]
    if subject:
        parts.append(json.dumps(subject, sort_keys=True))
    return profiles.digest(*parts)[:16]


def _scope_facts(task: dict, tasks: list[dict]) -> dict:
    reading = agreements.effective()
    if not reading["configured"]:
        return {"configured": False, "scope": None}
    sid, why = orchestrator.scope_of(task, reading)
    here = agreements.scope(reading, sid) if sid else None
    if here is None:
        return {"configured": True, "scope": None, "why": why}
    return {"configured": True, "scope": sid,
            "authority": {k: {"allowed": v["allowed"], "by": v["by"]}
                          for k, v in here["authority"].items()},
            "paused": bool(here.get("pausedBy")),
            "ceilings": {k: v.get("value") for k, v in
                         (here.get("ceilings") or {}).items()
                         if isinstance(v, dict)}}


def _evidence_facts(filename: str) -> list[dict]:
    out = []
    try:
        found = evidence.for_task(filename)
    except (OSError, ValueError):
        return out
    for record in sorted(found, key=lambda r: str(r.get("requested") or ""),
                         reverse=True)[:MAX_EVIDENCE_FACTS]:
        out.append({"id": record.get("id"), "check": record.get("check"),
                    "scope": record.get("scope"),
                    "outcome": record.get("outcome"),
                    "commit": ((record.get("source") or {}).get("commit") or "")[:12]
                    or None})
    return out


def _card_fact(card: dict) -> dict:
    return {"number": _number(card), "file": card["file"],
            "title": card.get("title"), "stage": card["stage"]}


def _scrubbed(doc: dict) -> dict:
    """The facts with credential-shaped text masked — except the binding,
    which is bench's own revisions and names and which a response must
    echo byte for byte: a long revision hash read as a token and masked
    would make every honest response a binding mismatch."""
    out = json.loads(redact.scrub(json.dumps(doc, sort_keys=True), "«redacted»"))
    if "binding" in doc:
        out["binding"] = json.loads(json.dumps(doc["binding"]))
    return out


def _ask_facts(ask: dict) -> dict:
    """The question, the conversation so far and bench's readings for it,
    as data in the facts document — never spliced into the prompt."""
    return {"question": ask["question"], "conversation": ask["conversation"],
            "scope": ask["target"]["kind"],
            "subject": ask["target"].get("subject") or "board",
            "snapshot": ask.get("snapshot"),
            "history": list(ask.get("history") or [])[-MAX_ASK_HISTORY:],
            "readings": ask.get("facts") or {},
            "answerShape": {
                "answer": "now, why, next (what happens by itself, if "
                          "anything) and unblock (who can, with which "
                          "existing control) — plain language",
                "findings": "recorded facts only, each citing evidence",
                "gaps": "[{id, gap}] what bench's records do not say; never "
                        "invent a cause",
                "suggestions": "[{id, suggestion, by, control?, card?, "
                               "evidence?}] — by: " + ", ".join(decisions.SUGGESTION_BY)
                               + "; control: " + ", ".join(decisions.CONTROLS),
                "questions": "always empty", "actions": "always empty"}}


def facts(filename: str | None, need: str, operation: str, bound: dict,
          tasks: list[dict], subject: dict | None = None,
          question: str | None = None, ask: dict | None = None) -> dict:
    """What bench tells the run in its own words: public readings only —
    no path, no environment value, no credential — scrubbed again on the
    way out because a title is a person's text."""
    if filename is None:
        # a board-wide question (task 181): no card, bench's board readings
        return _scrubbed({
            "kind": FACTS_KIND, "v": VERSION, "operation": operation,
            "need": need, "question": question or NEEDS[need],
            "card": None, "binding": bound, "ask": _ask_facts(ask),
            "contract": CONTRACT_FIELDS,
            "notes": ["Every fact here is bench's own reading at the moment the "
                      "run was prepared. Agent-reported claims are labelled as "
                      "such. Lists are bounded; what was left out is counted."]})
    cards = context_cards(filename, tasks)
    task = cards["task"]
    try:
        eligible = movements.eligibility(task, tasks)
        eligible = {"eligible": eligible["eligible"], "code": eligible["code"],
                    "reason": eligible["reason"]}
    except (OSError, ValueError, KeyError):
        eligible = {"eligible": None, "code": "unknown",
                    "reason": "movement history could not be read"}
    out = {
        "kind": FACTS_KIND, "v": VERSION,
        "operation": operation, "need": need,
        # the question as the run is asked it: the need's effective
        # guidance (task 153), or what the need is when none was composed
        "question": question if question is not None else NEEDS[need],
        "card": _card_fact(task),
        "phase": _card_fact(cards["phase"]) if cards["phase"] else None,
        "members": [_card_fact(m) for m in cards["members"]],
        "dependencies": [_card_fact(d) for d in cards["dependencies"]],
        "orchestration": _scope_facts(task, tasks),
        "eligibility": eligible,
        "evidence": _evidence_facts(filename),
        "binding": bound,
        # what bench observed about the incident a recovery diagnosis is
        # for (task 144): its signals, the failure's class and the attempts
        # so far — bench's readings, never the stalled run's own claims
        "incident": subject,
        "contract": CONTRACT_FIELDS,
        "notes": ["Every fact here is bench's own reading at the moment the "
                  "run was prepared. Agent-reported claims are not in it."],
    }
    if ask is not None:
        out["ask"] = _ask_facts(ask)
    return _scrubbed(out)


def _input_item(name: str, role: str, source: str, path: Path,
                data: bytes) -> dict:
    sha = _sha(data)
    return {"name": name, "source": source, "root": "work" if role != "facts" else "tool",
            "required": True, "readable": True, "status": "resolved",
            "sha256": sha, "bytes": len(data), "revision": f"sha:{sha[:12]}",
            "contextId": profiles.context_id(name, sha), "pinned": None,
            "path": str(path), "why": None, "remedy": None,
            "access": "read-only", "kind": "reasoning-input", "role": role}


def attach(found: dict, filename: str | None, need: str, operation: str,
           bound: dict, tasks: list[dict], subject: dict | None = None,
           question: str | None = None, ask: dict | None = None) -> dict:
    """Add the reasoning context to a launch's manifest, before the pin,
    the way a card's own inputs are added (`inputs.attach`) — so each is
    copied, hashed and granted by one exact read exactly as an approved
    input is. A file over the pin cap refuses the request. A board-wide
    question (task 181) is given bench's facts document alone."""
    context = found.setdefault("context", [])
    entries = []
    if filename is not None:
        cards = context_cards(filename, tasks)
        entries = [("reason-card", "card", cards["task"])]
        if cards["phase"] is not None:
            entries.append(("reason-phase", "phase", cards["phase"]))
        for number, member in enumerate(cards["members"][:12], 1):
            entries.append((f"reason-member-{number}", "member", member))
        for number, dep in enumerate(cards["dependencies"], 1):
            entries.append((f"reason-dependency-{number}", "dependency", dep))
    for name, role, card in entries:
        path = _card_file(card)
        data = path.read_bytes()
        if len(data) > profiles.MAX_CONTEXT_BYTES:
            raise ValueError(f"{card['file']} is over the "
                             f"{profiles.MAX_CONTEXT_BYTES}-byte pin cap, so it "
                             f"cannot be delivered to a reasoning run")
        context.append(_input_item(name, role,
                                   f"work:tasks/{card['stage']}/{card['file']}",
                                   path, data))
    written = facts(filename, need, operation, bound, tasks, subject, question, ask)
    facts_path = _dir() / "facts" / f"{operation}.json"
    records.write_json(facts_path, written)
    # named by what it is rather than by a path: the facts are bench's
    # own document, written for this operation alone
    context.append(_input_item("reason-facts", "facts",
                               f"bench:reasoning/facts/{operation}.json",
                               facts_path, facts_path.read_bytes()))
    return found


def delivered(found: dict) -> list[dict]:
    """After the pin: every reasoning input landed as the bytes bench read,
    or the launch refuses — a card that changed while the launch was
    prepared would be reasoned about at a revision the binding does not
    name."""
    out = []
    for item in found.get("context") or []:
        if item.get("kind") != "reasoning-input":
            continue
        if item.get("status") != "pinned" or item.get("pinnedSha256") != item["sha256"]:
            raise ValueError(f"the reasoning input {item['source']} changed or "
                             f"could not be pinned while the launch was being "
                             f"prepared — nothing was started")
        out.append({"name": item["name"], "role": item["role"],
                    "source": item["source"], "sha256": item["sha256"],
                    "pinned": item["pinned"]})
    return out


# ── the response contract ──────────────────────────────────────────────

CONTRACT_FIELDS = {
    "v": VERSION, "kind": KIND,
    "required": ["v", "kind", "operation", "need", "card", "inputs", "summary"],
    "lists": {k: {"max": MAX_ITEMS[k], "keys": sorted(ITEM_KEYS[k][0]),
                  "required": sorted(ITEM_KEYS[k][1])} for k in ITEM_KEYS},
    "actions": list(PROPOSABLE), "severities": list(SEVERITIES),
    "evidence": ["input:<name of an input delivered to this run>",
                 "card:<number of a card in this run's context>",
                 "evidence:<id of an evidence record in the facts>"],
    "bounds": {"block": MAX_BLOCK, "summary": MAX_SUMMARY, "text": MAX_TEXT,
               "options": MAX_OPTIONS, "references": MAX_REFS},
}


def contract_text(job: dict) -> str:
    """The response shape a run is told, filled with its own identity and
    binding so the example is the answer's frame, not a template."""
    example = {
        "v": VERSION, "kind": KIND, "operation": job["id"], "need": job["need"],
        "card": job["binding"]["card"], "inputs": job["binding"],
        "summary": "two to five public sentences: what you found and propose",
        "findings": [{"id": "f1", "statement": "one observed fact",
                      "severity": "info", "evidence": ["input:reason-card"]}],
        "questions": [{"id": "q1", "question": "a decision only the owner can make",
                       "options": ["option A", "option B"],
                       "recommendation": "option A", "why": "because …",
                       "evidence": ["input:reason-card"]}],
        "actions": [{"id": "a1", "action": "ask-owner",
                     "card": job["binding"]["card"], "why": "because …",
                     "evidence": ["input:reason-facts"]}],
        "blockers": [],
    }
    if job["need"] == "ask":
        # an answer explains: no questions opened, no actions proposed
        example.update({
            "summary": "one to three plain sentences answering the question",
            "answer": {"now": "what is happening now",
                       "why": "why, from the recorded facts — or that the "
                              "records do not say",
                       "next": "what happens next by itself, if anything",
                       "unblock": "who can unblock it, with which existing control"},
            "findings": [{"id": "f1", "statement": "one recorded fact",
                          "severity": "info", "evidence": ["input:reason-facts"]}],
            "gaps": [{"id": "g1", "gap": "what bench's records do not say"}],
            "suggestions": [{"id": "s1", "suggestion": "who could act and how",
                             "by": "you", "control": "open-card",
                             "evidence": ["input:reason-facts"]}],
            "questions": [], "actions": []})
    return json.dumps(example, indent=2, ensure_ascii=False)


def parse(log: str) -> dict:
    """The run's answer off its output: the last response block, or the
    last decline. Anything else is no answer."""
    found = list(BLOCK_RE.finditer(log))
    if found:
        return {"kind": "response", "raw": found[-1].group("body")}
    declines = [m.group(1).strip() for m in DECLINED_RE.finditer(log)
                if not m.group(1).strip().startswith("<")]
    if declines:
        return {"kind": "declined", "reason": declines[-1][:MAX_TEXT] or "no reason given"}
    return {"kind": "none"}


def _text(value, where: str, problems: list, limit: int = MAX_TEXT,
          required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where} must be a non-empty string")
        return None
    if len(value) > limit:
        problems.append(f"{where} is {len(value)} characters, over the {limit} bound")
        return None
    if _UNSAFE.search(value):
        problems.append(f"{where} carries a control or bidirectional character")
        return None
    if "<!--" in value:
        problems.append(f"{where} carries an HTML comment, which the board "
                        f"reserves for its own records")
        return None
    return redact.scrub(value, "«redacted»")


def _refs(value, where: str, problems: list, allowed: dict) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_REFS:
        problems.append(f"{where} must be a list of at most {MAX_REFS} references")
        return []
    out = []
    for ref in value:
        if not isinstance(ref, str) or not REF_RE.match(ref):
            problems.append(f"{where} holds {str(ref)[:60]!r}, which is not a "
                            f"reference (input:, card: or evidence:)")
            continue
        kind, _, name = ref.partition(":")
        if name not in allowed[kind]:
            problems.append(f"{where} cites {ref}, which this run was not given "
                            f"— a reference must name something delivered to it")
            continue
        out.append(ref)
    return out


def validate(job: dict, answer: dict, now_binding: dict | None) -> dict:
    """Server-side: what a response may become. Returns the job's new
    status with the validated public response, or the diagnostics that
    refused it. Nothing here executes anything, whatever it says."""
    if answer["kind"] == "declined":
        return {"status": "invalid", "diagnostics": [
            {"code": "declined", "why": f"the run declined: {answer['reason']}"}]}
    if answer["kind"] != "response":
        return {"status": "invalid", "diagnostics": [
            {"code": "no-response", "why": "the run ended without a response in "
                                          "the required shape"}]}
    raw = answer["raw"]
    if len(raw.encode("utf-8")) > MAX_BLOCK:
        return {"status": "invalid", "diagnostics": [
            {"code": "oversized", "why": f"the response is over {MAX_BLOCK} bytes"}],
            "raw": _raw(raw)}
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return {"status": "invalid", "diagnostics": [
            {"code": "malformed", "why": f"the response is not JSON: {exc}"}],
            "raw": _raw(raw)}
    problems: list[str] = []
    codes: list[str] = []
    if not isinstance(doc, dict):
        return {"status": "invalid", "diagnostics": [
            {"code": "malformed", "why": "the response is not a JSON object"}],
            "raw": _raw(raw)}
    asking = job["need"] == "ask"
    unknown = sorted(set(doc) - TOP_KEYS - (ASK_KEYS if asking else set()))
    if unknown:
        codes.append("unknown-field")
        problems.append(f"unknown field(s) {', '.join(unknown[:6])} — the contract "
                        f"has no place for private reasoning or anything else")
    for key in CONTRACT_FIELDS["required"]:
        if key not in doc:
            codes.append("malformed")
            problems.append(f"the required field {key!r} is missing")
    if doc.get("v") != VERSION or doc.get("kind") != KIND:
        codes.append("unsupported-version")
        problems.append(f"the response must say v {VERSION}, kind {KIND}")
    # identity: the response is about this operation, this need, this card
    if doc.get("operation") != job["id"]:
        codes.append("forged-identity")
        problems.append("the response names another operation than the one "
                        "this run was launched for")
    if doc.get("need") != job["need"]:
        codes.append("forged-identity")
        problems.append("the response answers another need than the one asked")
    if str(doc.get("card")) != str(job["binding"]["card"]):
        codes.append("out-of-scope")
        problems.append("the response is about another card than this run's")
    # binding: the revisions it echoes are the ones it was given
    if doc.get("inputs") != job["binding"]:
        codes.append("binding-mismatch")
        problems.append("the input revisions the response names are not the "
                        "ones this run was launched with")

    allowed = {"input": {i["name"] for i in job.get("inputs") or []},
               "card": set(job.get("cards") or []),
               "evidence": {e for e in job.get("evidenceIds") or [] if e}}
    in_scope = set(job.get("scopeCards") or [])
    out = {"v": VERSION, "kind": KIND, "operation": job["id"],
           "need": job["need"], "card": job["binding"]["card"],
           "summary": _text(doc.get("summary"), "summary", problems, MAX_SUMMARY)}
    for key, (keys, needed) in ITEM_KEYS.items():
        items = doc.get(key, [])
        if not isinstance(items, list):
            problems.append(f"{key} must be a list")
            continue
        if len(items) > MAX_ITEMS[key]:
            codes.append("oversized")
            problems.append(f"{key} holds {len(items)} items, over the "
                            f"{MAX_ITEMS[key]} bound")
            continue
        kept, ids = [], set()
        for n, item in enumerate(items):
            where = f"{key}[{n}]"
            if not isinstance(item, dict):
                problems.append(f"{where} is not an object")
                continue
            extra = sorted(set(item) - keys)
            missing = sorted(needed - set(item))
            if extra:
                codes.append("unknown-field")
                problems.append(f"{where} has unknown field(s) {', '.join(extra[:4])}")
            if missing:
                problems.append(f"{where} is missing {', '.join(missing)}")
            if extra or missing:
                continue
            if not isinstance(item["id"], str) or not ITEM_ID.match(item["id"]) \
                    or item["id"] in ids:
                problems.append(f"{where} needs a unique short id")
                continue
            ids.add(item["id"])
            entry = {"id": item["id"],
                     "evidence": _refs(item.get("evidence"), f"{where}.evidence",
                                       problems, allowed)}
            if key == "findings":
                entry["statement"] = _text(item["statement"], f"{where}.statement", problems)
                severity = item.get("severity", "info")
                if severity not in SEVERITIES:
                    problems.append(f"{where}.severity must be one of "
                                    f"{', '.join(SEVERITIES)}")
                entry["severity"] = severity
            elif key == "questions":
                entry["question"] = _text(item["question"], f"{where}.question", problems)
                options = item.get("options", [])
                if not isinstance(options, list) or len(options) > MAX_OPTIONS:
                    problems.append(f"{where}.options must be a list of at most "
                                    f"{MAX_OPTIONS}")
                    options = []
                entry["options"] = [_text(o, f"{where}.options", problems)
                                    for o in options]
                entry["recommendation"] = _text(item.get("recommendation"),
                                                f"{where}.recommendation",
                                                problems, required=False)
                entry["why"] = _text(item.get("why"), f"{where}.why", problems,
                                     required=False)
            elif key == "actions":
                if item["action"] not in PROPOSABLE:
                    codes.append("action-unknown")
                    problems.append(f"{where}.action {str(item['action'])[:40]!r} "
                                    f"is not one of {', '.join(PROPOSABLE)}")
                    continue
                if str(item["card"]) not in in_scope:
                    codes.append("out-of-scope")
                    problems.append(f"{where} proposes acting on card "
                                    f"{str(item['card'])[:20]}, outside this "
                                    f"run's scope")
                    continue
                entry.update({"action": item["action"], "card": str(item["card"]),
                              "why": _text(item["why"], f"{where}.why", problems),
                              # a proposal, never a grant: whatever acts on it
                              # asks orchestrator.authorize again first
                              "executable": False})
            else:
                entry["blocker"] = _text(item["blocker"], f"{where}.blocker", problems)
            kept.append(entry)
        out[key] = kept
    if asking:
        _validate_ask(doc, out, problems, codes, allowed)
    if problems:
        return {"status": "invalid",
                "diagnostics": [{"code": c, "why": c} for c in sorted(set(codes))]
                + [{"code": "problem", "why": p} for p in problems[:30]],
                "raw": _raw(raw)}
    if now_binding is not None and now_binding != job["binding"]:
        changed = sorted(k for k in set(now_binding) | set(job["binding"])
                         if now_binding.get(k) != job["binding"].get(k))
        return {"status": "stale", "response": out, "diagnostics": [
            {"code": "stale", "why": f"the inputs changed while the run "
                                     f"reasoned ({', '.join(changed)}); the "
                                     f"response is kept as history and the "
                                     f"need must be re-evaluated"}]}
    return {"status": "valid", "response": out, "diagnostics": []}


def _validate_ask(doc: dict, out: dict, problems: list, codes: list,
                  allowed: dict) -> None:
    """The extra shape of an answer to a person's question (task 181):
    what is happening now, why, what happens next by itself and who can
    unblock it — then the recorded facts (findings, each citing evidence),
    the gaps (what bench's records do not say) and suggestions (who could
    act, through which existing control). Asking explains; it proposes no
    action and opens no question, so both lists must be empty."""
    for key in ("questions", "actions"):
        if out.get(key):
            codes.append("ask-not-act")
            problems.append(f"an answer to a person's question carries no {key} "
                            f"— asking explains; acting is a separate control")
    answer = doc.get("answer")
    if not isinstance(answer, dict) or set(answer) != {"now", "why", "next", "unblock"}:
        codes.append("malformed")
        problems.append("answer must be an object with exactly now, why, next "
                        "and unblock")
        answer = {}
    out["answer"] = {part: _text(answer.get(part), f"answer.{part}", problems)
                     for part in ("now", "why", "next", "unblock")} if answer else {}
    for n, finding in enumerate(out.get("findings") or []):
        if not finding.get("evidence"):
            codes.append("unsupported-fact")
            problems.append(f"findings[{n}] cites no evidence — a recorded fact "
                            f"names the record it comes from, or it belongs in gaps")
    gaps = doc.get("gaps", [])
    kept = []
    if not isinstance(gaps, list) or len(gaps) > MAX_ASK_ITEMS["gaps"]:
        problems.append(f"gaps must be a list of at most {MAX_ASK_ITEMS['gaps']}")
        gaps = []
    for n, gap in enumerate(gaps):
        if not isinstance(gap, dict) or set(gap) != {"id", "gap"} \
                or not isinstance(gap.get("id"), str) or not ITEM_ID.match(gap["id"]):
            problems.append(f"gaps[{n}] must be {{id, gap}} with a short id")
            continue
        kept.append({"id": gap["id"], "gap": _text(gap["gap"], f"gaps[{n}].gap", problems)})
    out["gaps"] = kept
    suggestions = doc.get("suggestions", [])
    kept = []
    if not isinstance(suggestions, list) or \
            len(suggestions) > MAX_ASK_ITEMS["suggestions"]:
        problems.append(f"suggestions must be a list of at most "
                        f"{MAX_ASK_ITEMS['suggestions']}")
        suggestions = []
    keys, needed = ASK_SUGGESTION_KEYS
    for n, item in enumerate(suggestions):
        where = f"suggestions[{n}]"
        if not isinstance(item, dict) or set(item) - keys or needed - set(item):
            codes.append("unknown-field")
            problems.append(f"{where} must hold {', '.join(sorted(needed))} and "
                            f"optionally control, card, evidence")
            continue
        if not isinstance(item["id"], str) or not ITEM_ID.match(item["id"]):
            problems.append(f"{where} needs a short id")
            continue
        if item["by"] not in decisions.SUGGESTION_BY:
            problems.append(f"{where}.by must be one of "
                            f"{', '.join(decisions.SUGGESTION_BY)}")
            continue
        control = item.get("control")
        if control is not None and control not in decisions.CONTROLS:
            codes.append("control-unknown")
            problems.append(f"{where}.control {str(control)[:40]!r} is not one of "
                            f"{', '.join(decisions.CONTROLS)}")
            continue
        card = item.get("card")
        if card is not None and str(card) not in allowed["card"]:
            codes.append("out-of-scope")
            problems.append(f"{where} names card {str(card)[:20]}, which is not "
                            f"in this run's context")
            continue
        kept.append({"id": item["id"],
                     "suggestion": _text(item["suggestion"], f"{where}.suggestion",
                                         problems),
                     "by": item["by"], "control": control,
                     "card": str(card) if card is not None else None,
                     "evidence": _refs(item.get("evidence"), f"{where}.evidence",
                                       problems, allowed),
                     # pointing at a control is not pressing it
                     "executable": False})
    out["suggestions"] = kept


def _raw(text: str) -> dict:
    """What is kept of a refused response: bounded, scrubbed, hashed —
    retained to diagnose, never read as an instruction."""
    data = text.encode("utf-8")
    kept = redact.scrub(data[:MAX_RAW].decode("utf-8", "replace"), "«redacted»")
    return {"sha256": _sha(data), "bytes": len(data), "clipped": len(data) > MAX_RAW,
            "text": _UNSAFE.sub("?", kept), "inert": True}


# ── reading ────────────────────────────────────────────────────────────


def freshness(job: dict, board: dict | None = None) -> dict:
    """Whether a settled response still describes the card. Compared,
    never repaired: a stale response is history, and the need is asked
    again under a new fingerprint."""
    try:
        now = _binding_for(job, board)
    except (OSError, ValueError):
        return {"fresh": False, "why": f"{job['task']} is no longer on the board"}
    if now != job["binding"]:
        changed = sorted(k for k in set(now) | set(job["binding"])
                         if now.get(k) != job["binding"].get(k))
        return {"fresh": False, "why": f"changed since: {', '.join(changed)}"}
    return {"fresh": True, "why": None}


PUBLIC = ("v", "id", "need", "task", "card", "stage", "via", "scope", "actor",
          "attempt", "retryOf", "status", "created", "started", "ended",
          "agent", "name", "harness", "model", "policyId", "binding",
          "inputs", "response", "diagnostics", "raw", "error", "usage",
          "fingerprint", "provenance", "cardRecord", "action", "subject",
          "prompt", "ask")


def public(job: dict) -> dict:
    """What later members (on-card history, the Orchestrator view) read:
    status, usage, the validated response or the diagnostics, and
    provenance — no path, no private reasoning, no credential."""
    out = {key: job[key] for key in PUBLIC if key in job}
    out["inputs"] = [{k: i[k] for k in ("name", "role", "source", "sha256")
                      if k in i} for i in job.get("inputs") or []]
    if job.get("status") == "valid":
        out["fresh"] = freshness(job)
    return out


def for_card(filename: str) -> list[dict]:
    """Every reasoning job this board holds for a card, newest first.
    A read: it launches nothing and writes nothing."""
    with LOCK:
        _load()
        found = [j for j in JOBS.values() if j.get("task") == filename]
    return [public(j) for j in sorted(found, key=lambda j: (j.get("created") or 0,
                                                            j["id"]), reverse=True)]


def for_subject(incident: str, attempt: int) -> dict | None:
    """The newest job launched for one recovery incident's attempt (task
    144), whole — `recovery` reads its status, response and error to
    settle the attempt, and its absence to prove nothing ran. A read."""
    with LOCK:
        _load()
        found = [j for j in JOBS.values()
                 if (j.get("subject") or {}).get("incident") == incident
                 and (j.get("subject") or {}).get("attempt") == attempt]
    found.sort(key=lambda j: (j.get("created") or 0, j["id"]))
    return dict(found[-1]) if found else None


def for_operation(operation: str) -> dict | None:
    """One job by its operation id, public — how a conversation's reply
    names the run that gave it (task 181). A read."""
    with LOCK:
        _load()
        job = JOBS.get(operation)
        return public(job) if job is not None else None


def active_for(filename: str | None, conversation: str | None = None) -> dict | None:
    """The reasoning run in flight for a card, or for a board conversation
    (task 181) — one at a time. A read."""
    with LOCK:
        _load()
        for job in JOBS.values():
            if job.get("status") not in ACTIVE:
                continue
            if filename and job.get("task") == filename:
                return public(job)
            if conversation and (job.get("ask") or {}).get("conversation") == conversation:
                return public(job)
    return None


def facts_document(operation: str) -> dict | None:
    """The facts document bench wrote for one run — its own public readings,
    scrubbed when written. A read."""
    if not re.match(r"^rs-[0-9a-f]{10}-\d{1,4}$", str(operation or "")):
        return None
    found = records.read_json(_dir() / "facts" / f"{operation}.json")
    return found if isinstance(found, dict) else None


def for_ask(conversation: str, ask_record: str) -> list[dict]:
    """Every job launched for one question in a conversation, oldest
    first, public (task 181). A read."""
    with LOCK:
        _load()
        found = [j for j in JOBS.values()
                 if (j.get("ask") or {}).get("conversation") == conversation
                 and (j.get("ask") or {}).get("record") == ask_record]
    return [public(j) for j in sorted(found, key=lambda j: (j.get("created") or 0,
                                                            j["id"]))]


def board() -> dict:
    """Per card, the newest job's status — what a card face needs."""
    out: dict = {}
    with LOCK:
        _load()
        for job in sorted(JOBS.values(), key=lambda j: j.get("created") or 0):
            if not job.get("task"):
                continue                   # a board-wide question has no card
            out[job["task"]] = {"id": job["id"], "need": job["need"],
                                "status": job["status"]}
    return out


def readiness() -> dict:
    """Whether this board can launch a reasoning run at all, through the
    resolver a launch rides — and nothing launched to find out."""
    found = profiles.resolve("reason")
    return {"ready": not found["blockers"], "blockers": list(found["blockers"]),
            "codes": [d["code"] for d in found.get("diagnostics") or []],
            "selection": found["selection"]}


# ── asking ─────────────────────────────────────────────────────────────


def request(filename: str | None, need: str, *, via: str = "orchestrator",
            retry: bool = False, board: dict | None = None,
            actor: str | None = None, action: str = "reason",
            subject: dict | None = None, ask: dict | None = None) -> dict:
    """Ask one reasoning question about one card, at most once per input
    binding.

    `via` is who asks: `orchestrator` (authorized by the agreement, with
    its capacity, and counted in the launch ledger) or `person` (an
    explicit click; no agreement needed, still resolved through the same
    profile and refused where the harness cannot run it). The same
    question about unchanged inputs answers with the job it already has —
    running, or settled — so a scheduler tick, a refresh or a concurrent
    caller never launches twice; a job that failed or was cancelled is
    asked again only with `retry`.

    `action` is the orchestrator action the launch is authorized and
    counted as: `reason`, or `recover` for a recovery diagnosis (task 144),
    which the `recovery` authority word delegates instead and which
    carries the incident it serves in `subject` — folded into the
    fingerprint and handed to the run in its facts.

    `ask` (task 181) is a person's question, only with the `ask` need:
    `{target, conversation, record, question, history, snapshot, facts,
    cards}`. The `ask` record is already in its conversation, so the
    binding names it; `target.kind` `board` has no card (`filename` is
    None) and is bound to the board and its conversation instead. The
    question is data in the facts document, never part of the prompt."""
    if (need == "ask") != (ask is not None):
        raise ValueError("the ask need carries a person's question, and only it does")
    if need == "ask" and (via != "person" or action != "reason"):
        raise ValueError("only a person asks the orchestrator a question")
    board_kind = ask is not None and ask["target"]["kind"] == "board"
    if (filename is None) != board_kind:
        raise ValueError("a question names a card unless it is about the board")
    if action not in ("reason", "recover"):
        raise ValueError("a reasoning launch is authorized as reason or recover")
    if need not in NEEDS:
        raise ValueError(f"unknown reasoning need {need!r} — one of "
                         f"{', '.join(NEEDS)}")
    if via not in ("orchestrator", "person"):
        raise ValueError("a reasoning request comes from the orchestrator or a person")
    with LOCK:
        _load()
        board = taskfiles.collect() if board is None else board
        tasks = _tasks(board)
        bound = (board_binding(ask["conversation"], board) if board_kind
                 else binding(filename, board))
        keyed = subject if ask is None else {"conversation": ask["conversation"],
                                             "ask": ask["record"]}
        print_ = fingerprint(need, bound, keyed)
        prior = sorted((j for j in JOBS.values() if j.get("fingerprint") == print_),
                       key=lambda j: (j.get("created") or 0, j["id"]))
        last = prior[-1] if prior else None
        if last is not None and (last["status"] in ACTIVE | SETTLED):
            return {"job": public(last), "duplicate": True}
        if last is not None and not retry:
            raise Conflict(f"this question about these inputs was asked already "
                           f"and its last attempt {last['status']} — retry it "
                           f"deliberately")
        if board_kind:
            busy = [j for j in JOBS.values() if j.get("status") in ACTIVE
                    and (j.get("ask") or {}).get("conversation") == ask["conversation"]]
        else:
            busy = [j for j in JOBS.values() if j.get("task") == filename
                    and j.get("status") in ACTIVE]
        if busy:
            raise ValueError(f"a reasoning run is already in flight for "
                             f"{filename or 'this conversation'} "
                             f"({busy[0]['need']}); one at a time")
        label = filename or f"board-{ask['conversation']}"

        authorized = None
        if via == "orchestrator":
            authorized = orchestrator.authorize(action, filename, board=board)
            if not authorized["allowed"]:
                raise ValueError(f"not authorized: {authorized['reason']} "
                                 f"({authorized['code']})")

        stage = bound.get("stage") or "board"
        operation = f"rs-{print_[:10]}-{len(prior) + 1}"
        # the prompt first (task 153): the shared template and this need's
        # guidance, each held to its contract, exactly as Settings → Agents
        # shows them. A broken local override refuses here, before a
        # manifest, a pin, a job or a process exists — never repaired,
        # never skipped for the built-in.
        prompts = {"main": templates.require("reason.md"),
                   "guidance": templates.require(templates.need_template(need))}
        question = templates.render(
            prompts["guidance"]["name"], prompts["guidance"]["text"],
            {"card": bound["card"], "stage": stage, "filename": label}).strip()
        # resolved, context attached, pinned and confirmed before anything
        # is recorded: a refusal leaves no job and no launch
        found = profiles.manifest("reason", label, stage)
        profiles.require(found)
        found["template"] = {**templates.identity(prompts["main"]),
                             "guidance": templates.identity(prompts["guidance"])}
        attach(found, filename, need, operation, bound, tasks, subject, question, ask)
        profiles.pin(found)
        inputs_ = delivered(found)

        if board_kind:
            scope_cards, context_numbers = set(), set()
        else:
            cards = context_cards(filename, tasks)
            scope_cards = {bound["card"]} | {(_number(m) or m["file"])
                                             for m in cards["members"]}
            context_numbers = {(_number(c) or c["file"]) for c in
                               [cards["task"],
                                *([cards["phase"]] if cards["phase"] else []),
                                *cards["members"], *cards["dependencies"]]}
        if ask is not None:
            # the cards bench's readings for the question named
            context_numbers |= {str(c) for c in (ask.get("cards") or [])
                                [:MAX_ASK_CARDS]}
        selection = found["selection"]
        job = {
            "v": VERSION, "id": operation, "need": need, "task": filename,
            "card": bound["card"], "stage": stage, "via": via,
            "scope": (authorized or {}).get("scope"),
            "actor": actor or (taskfiles.actor_name() or "unattributed"),
            "attempt": len(prior) + 1,
            "retryOf": last["id"] if last is not None else None,
            "fingerprint": print_, "binding": bound, "status": "queued",
            "action": action, "subject": subject,
            "created": time.time(), "inputs": inputs_,
            "cards": sorted(context_numbers), "scopeCards": sorted(scope_cards),
            "evidenceIds": ([e["id"] for e in _evidence_facts(filename)]
                            if filename else [])
                           + [str(e) for e in ((ask or {}).get("evidence") or [])],
            "label": label,
            "ask": None if ask is None else {
                "target": ask["target"], "conversation": ask["conversation"],
                "record": ask["record"], "snapshot": ask.get("snapshot")},
            "harness": selection["harness"]["name"],
            "model": selection["model"]["value"] or None,
            "policyId": found["policyId"],
            "usage": {"launches": 1, "pool": "reasoning",
                      "counted": ("the orchestrator launch ledger" if via == "orchestrator"
                                  else "running-agent capacity only (a person's launch)"),
                      "tokens": "not reported", "cost": "not reported"},
            "provenance": {
                "authorized": (authorized or {}).get("provenance")
                if authorized else ["a person's explicit request"],
                "modelSource": selection["model"]["source"],
                "modelSetting": selection["model"]["setting"],
                "bundle": (found.get("bundle") or {}).get("revision")},
        }
        JOBS[job["id"]] = job
        _save(job)
        try:
            _launch(job, found, prompts)
        except ValueError as exc:
            job.update({"status": "failed", "ended": time.time(),
                        "error": f"the reasoning run could not be launched: {exc}"})
            _save(job)
            raise
        if via == "orchestrator":
            orchestrator.record_launch(job["scope"], bound["card"], action, job["id"])
        return {"job": public(job), "duplicate": False}


def _inputs_text(job: dict) -> str:
    lines = []
    for item in job["inputs"]:
        lines.append(f"- `input:{item['name']}` ({item['role']}, from "
                     f"{item['source']}, sha256 {item['sha256'][:12]}): "
                     f"`{item['pinned']}`")
    return "\n".join(lines)


def _launch(job: dict, found: dict, prompts: dict | None = None) -> None:
    filename = job["task"] or job["label"]
    agent_id = (f"reason-{filename[:-3][:40]}-{time.strftime('%H%M%S')}-"
                f"{secrets.token_hex(2)}")
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = profiles.record(agent_id, found)
    fields = config.prompt_fields(filename, job["stage"])
    # the same composition Settings → Agents previews (task 153), from the
    # texts the preflight validated: card, agreement and history text are
    # inserted as data, never re-read as a template
    prompts = prompts or {}
    composed = templates.compose_reasoning(job["need"], {
        "operation": job["id"], "need": job["need"], "filename": filename,
        "stage": job["stage"], "card": job["card"],
        "inputs": _inputs_text(job), "contract": contract_text(job),
        "guidance_read_only": fields["guidance_read_only"],
        "context": profiles.prompt_block(manifest)},
        main=prompts.get("main"), guidance=prompts.get("guidance"))
    prompt = composed["text"]
    job["prompt"] = composed["identity"]
    proc, log_file, model = agents.launch("reason", prompt, config.REPO,
                                          agent_id, filename, log_path, manifest)
    name = agents.pick_name(filename)
    record = {
        "id": agent_id, "task": filename, "branch": None, "worktree": None,
        "base": None, "status": "running", "rc": None, "started": time.time(),
        "session": None, "log": str(log_path), "proc": proc,
        "origin": job["stage"], "mode": "reason", "name": name,
        "model": model or None, "profile": profiles.summary(manifest),
        "reasoningJob": job["id"],
    }
    with state.LOCK:
        state.AGENTS[agent_id] = record
    job.update({"status": "running", "started": time.time(), "agent": agent_id,
                "name": name})
    _save(job)
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": (f"{name} is reasoning about {filename} ({job['need']}, "
                    f"{job['id']}) — it proposes; nothing is acted on from it")})
    threading.Thread(target=_reap, args=(job["id"], agent_id, proc, log_file),
                     daemon=True).start()


def _reap(job_id: str, agent_id: str, proc, log_file) -> None:
    record, stopped, rc = agents.finish(agent_id, proc, log_file)
    settle(job_id, record, stopped, rc)


def settle(job_id: str, record: dict, stopped: bool, rc: int) -> dict:
    """Close a job from its run's ending: cancelled, failed, or the
    validated response (valid, invalid or stale). Public for the reaper
    and for a test that plays one."""
    with LOCK:
        job = JOBS[job_id]
        if stopped:
            job.update({"status": "cancelled",
                        "error": "cancelled — no response, nothing acted on"})
        elif rc != 0:
            job.update({"status": "failed",
                        "error": f"the run exited rc={rc}: {agents.died_on(record)}"})
        else:
            try:
                log = Path(record["log"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                log = ""
            try:
                now = _binding_for(job)
            except (OSError, ValueError):
                now = {}
            job.update(validate(job, parse(log), now))
        job["ended"] = time.time()
        _save(job)
        status, filename = job["status"], job["task"] or job.get("label")
        name = job.get("name") or "the agent"
        settled = dict(job)
    on_card(settled)
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": {
            "valid": f"{name} answered {job['need']} for {filename} — a proposal; "
                     f"nothing was acted on",
            "invalid": f"{name}'s reasoning about {filename} was refused and "
                       f"kept as a diagnostic — nothing was acted on",
            "stale": f"{name}'s reasoning about {filename} is stale — the card "
                     f"changed while it ran; kept as history",
            "cancelled": f"{name}'s reasoning about {filename} was cancelled",
        }.get(status, f"{name}'s reasoning about {filename} failed — "
                      f"{job.get('error')}")[:300]})
    state.broadcast({"type": "board"})
    state.broadcast({"type": "agents"})
    return public(job)


def on_card(job: dict) -> dict:
    """Put a settled job's public outcome on its card (task 140) and note
    on the job what became of that, truthfully. A card write that fails —
    the card was archived, a record over its bound — never changes the
    job's outcome; the job says the card does not hold it."""
    try:
        result = decisions.record_response(job)
        found = {"written": bool(result.get("written")) or bool(result.get("duplicate")),
                 "committed": result.get("committed"), "record": result.get("id"),
                 "opened": result.get("opened") or []}
    except (ValueError, OSError) as exc:
        found = {"written": False, "error": str(exc)[:300]}
    with LOCK:
        current = JOBS.get(job["id"])
        if current is not None:
            current["cardRecord"] = found
            _save(current)
    return found


def cancel(job_id: str) -> dict:
    with LOCK:
        _load()
        job = JOBS.get(job_id)
        if job is None:
            raise ValueError("unknown reasoning job")
        if job["status"] not in ACTIVE or not job.get("agent"):
            raise ValueError(f"the reasoning job is {job['status']}, not running")
        agent_id = job["agent"]
    agents.stop_agent(agent_id)
    return public(job)
