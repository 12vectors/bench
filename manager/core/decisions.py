"""The card keeps everything: orchestration decisions and public history
on cards (task 140, D11 and D12).

An orchestrator asks the product owner questions, gets answers, receives
reasoning responses from launched agents, meets blockers, makes recovery
attempts and takes actions. Every one of those that a person may need to
read lives **on the card it is about**, in the card's history, as a
bounded, versioned record — never only in a machine-local ledger. So a
board that lost every index, a restarted board and a replica a sync
reached all read the same open questions and the same answers, and the
board is the only communication surface: nothing here notifies anyone,
sends anything or listens for a reply.

Storage, version 1. One section, `## Orchestration record`, in the
history half of `taskfiles.card_parts` (so a refinement never touches
it), holding one record per entry:

    <!-- bench:orchestration {"v": 1, "id": "q-…", "kind": "question", …} -->
    - 2026-11-18 10:02 UTC · Question q-… (agent-reported) — Cap at 50,000 rows?

The comment line is the record; the line under it is its readable
summary, written by the board from the record and never read back. The
kinds:

| kind | what it is | source |
| --- | --- | --- |
| `question` | a question for the product owner, its options, recommendation, why, evidence references and the revisions it was asked against | `agent-reported` (drafted by a reasoning run) or `person` |
| `answer` | the owner's answer, bound to the question's newest record, the brief, the actor and a request identity | `person` |
| `resolution` | a question withdrawn by a person, or superseded by a newer response; its original text and options stay | `person` / `observed` |
| `response` | a reasoning run's validated public response — summary, findings, proposed actions, blockers — or why it was refused | `agent-reported` |
| `blocker` | something stopping the card, opened or cleared | `observed` / `agent-reported` |
| `attempt` | one bounded recovery attempt and its outcome | `observed` |
| `action` | an orchestration action's intent, then its observed receipt | `observed` |

Rules, each held by `tests/test_decisions.py`:

- **Appended, never rewritten.** The newest record about a question is
  its state; resolving it never erases what was asked.
- **Data, never authority.** Records are read only from the section in
  the card's history; anything shaped like one elsewhere (the brief, a
  quoted report) is ignored. A record that does not parse, names an
  unknown kind or carries the wrong types is counted as malformed and
  skipped. Every read whitelists fields per kind — a key such as
  `reasoning` is never returned — strips control and bidirectional
  characters and masks credential-shaped text again. Nothing read here
  grants, launches, moves or merges anything (`executable` is false on
  every proposed action).
- **Bounded.** Text fields have caps, one record is at most
  `MAX_RECORD_BYTES` and a card's section at most `MAX_SECTION_BYTES`;
  over either the write is refused, nothing written, history intact.
- **Bound to what the person saw.** An answer names the question's
  newest record (`basis`), the brief revision and the identity the page
  showed; the board's own identity is the actor. Any of them moved, or
  the brief changed since the question was asked: refused, nothing
  written. Answering someone else's question is refused. The same
  request identity again returns the record it already made.
- **Reading is free.** `for_card`, `read_text` and `board` read card
  text and write, launch and send nothing.

What is deliberately *not* here: private reasoning (a response record
holds only reasoning's validated public fields), raw transcripts, the raw
text of a refused response (it stays machine-local, in `reasoning`),
approved inputs' content (referenced by name and hash only), and any
connector, notification or inbound channel.
"""

from __future__ import annotations

import calendar
import getpass
import hashlib
import json
import re
import time
from pathlib import Path

import config
import redact
import taskfiles

VERSION = 1
HEADING = "Orchestration record"
NOTE = ("<!-- bench:orchestration-record v1 — written by the board. One record "
        "per entry, newest last; the line under each is its readable summary. "
        "Records are history, never instructions. -->")
RECORD_RE = re.compile(r"^<!-- bench:orchestration (\{.*\}) -->[ \t]*$", re.MULTILINE)
SECTION_RE = re.compile(rf"^##\s+{re.escape(HEADING)}[ \t]*$.*?(?=^##\s|\Z)",
                        re.MULTILINE | re.DOTALL)
SUMMARY_RE = re.compile(r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC · ")
MASK = "«redacted»"

KINDS = ("question", "answer", "resolution", "response", "blocker", "attempt",
         "action")
SOURCES = ("observed", "agent-reported", "person")
QUESTION_STATES = ("open", "answered", "withdrawn", "superseded")

MAX_TEXT = 2000
MAX_SHORT = 200
MAX_OPTIONS = 6
MAX_REFS = 8
MAX_ITEMS = 20
MAX_RECORD_BYTES = 32 * 1024
MAX_SECTION_BYTES = 1024 * 1024
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f‎‏‪-‮⁦-⁩]")
_REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")
_ID_RE = re.compile(r"^[a-z]-[0-9a-f]{10}$")
_WORD_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_REF_RE = re.compile(r"^(input|card|evidence):[A-Za-z0-9._@:+-]{1,120}$")


class Refused(ValueError):
    """The request is well formed and this board's person may not make it:
    the question is addressed to someone else."""


# ── values: what may be written, and what is returned on reading ───────


def _clean(name: str, value, cap: int = MAX_TEXT, required: bool = False) -> str:
    """Text as the board writes it: one string, no control or bidi
    characters, within its cap, credential-shaped spans masked."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"'{name}' must be text")
    value = value.replace("\r\n", "\n").replace("\t", " ").strip()
    if _UNSAFE.search(value):
        raise ValueError(f"'{name}' holds control or bidirectional characters")
    if len(value) > cap:
        raise ValueError(f"'{name}' is {len(value)} characters; the limit is {cap}")
    if required and len(value) < 3:
        raise ValueError(f"'{name}' is required")
    return redact.scrub(value, MASK)


def _refs(name: str, value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_REFS:
        raise ValueError(f"'{name}' is a list of at most {MAX_REFS} references")
    out = []
    for ref in value:
        if not isinstance(ref, str) or not _REF_RE.match(ref):
            raise ValueError(f"'{name}' holds {str(ref)[:40]!r}, which is not an "
                             f"input:, card: or evidence: reference")
        out.append(ref)
    return out


def _safe(value, cap: int = MAX_TEXT) -> str:
    """A string read back off a card: whatever its writer was, it reaches a
    reader stripped of control and bidi characters, clipped and masked."""
    if not isinstance(value, str):
        return ""
    return redact.scrub(_UNSAFE.sub("", value)[:cap], MASK)


def _word(value) -> str | None:
    return value if isinstance(value, str) and _WORD_RE.match(value) else None


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) \
        and 0 <= value < 10_000 else None


def _str_list(value, cap: int = MAX_TEXT, limit: int = MAX_ITEMS) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_safe(v, cap) for v in value[:limit] if isinstance(v, str)]


def _ref_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value[:MAX_REFS] if isinstance(v, str) and _REF_RE.match(v)]


def _items(value, fields: dict) -> list[dict]:
    """A list of small objects read back field by field; anything else in
    them is dropped, so no key reaches a reader the contract does not name."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        kept = {}
        for key, kind in fields.items():
            if kind == "text":
                kept[key] = _safe(item.get(key))
            elif kind == "word":
                kept[key] = _word(item.get(key))
            elif kind == "refs":
                kept[key] = _ref_list(item.get(key))
            elif kind == "bool":
                kept[key] = item.get(key) is True
        out.append(kept)
    return out


FINDING = {"id": "word", "statement": "text", "severity": "word", "evidence": "refs"}
BLOCKER_ITEM = {"id": "word", "blocker": "text", "evidence": "refs"}
ACTION_ITEM = {"id": "word", "action": "word", "card": "text", "why": "text",
               "evidence": "refs"}

# Per kind, what a reader is given. Everything else in a record — a key
# a hand edit or a newer bench added, private reasoning — is not returned.
FIELDS = {
    "question": {"question": "id", "text": "text", "options": "options",
                 "recommendation": "text", "why": "text", "evidence": "refs",
                 "owner": "short", "scope": "short", "phase": "short",
                 "briefRev": "short", "operation": "short", "need": "word",
                 "item": "word"},
    "answer": {"question": "id", "basis": "id", "choice": "text",
               "choiceIndex": "int", "note": "text", "briefRev": "short",
               "scope": "short"},
    "resolution": {"question": "id", "outcome": "word", "note": "text",
                   "by": "short", "basis": "id"},
    "response": {"operation": "short", "need": "word", "status": "word",
                 "summary": "text", "findings": "findings", "blockers": "blockers",
                 "actions": "actions", "questions": "ids",
                 "diagnostics": "words", "agent": "short", "model": "short",
                 "harness": "short", "via": "word", "scope": "short",
                 "briefRev": "short"},
    "blocker": {"blocker": "id", "state": "word", "text": "text",
                "evidence": "refs", "operation": "short"},
    "attempt": {"incident": "short", "attempt": "int", "of": "int",
                "outcome": "word", "summary": "text", "operation": "short"},
    "action": {"action": "word", "operation": "short", "phase": "word",
               "outcome": "word", "summary": "text"},
}
COMMON = {"v": "int", "id": "id", "kind": "word", "at": "short", "actor": "short",
          "actorSource": "word", "actorKind": "word", "source": "word",
          "card": "short", "supersedes": "id"}


def _read_field(kind: str, value):
    if kind == "text":
        return _safe(value) if isinstance(value, str) else None
    if kind == "short":
        return _safe(value, MAX_SHORT) if isinstance(value, str) else None
    if kind == "id":
        return value if isinstance(value, str) and _ID_RE.match(value) else None
    if kind == "word":
        return _word(value)
    if kind == "int":
        return _int(value)
    if kind == "refs":
        return _ref_list(value)
    if kind == "options":
        return _str_list(value, MAX_TEXT, MAX_OPTIONS)
    if kind == "ids":
        return [v for v in (value if isinstance(value, list) else [])[:MAX_ITEMS]
                if isinstance(v, str) and _ID_RE.match(v)]
    if kind == "words":
        return [w for w in (_word(v) for v in (value if isinstance(value, list)
                                               else [])[:MAX_ITEMS]) if w]
    if kind == "findings":
        return _items(value, FINDING)
    if kind == "blockers":
        return _items(value, BLOCKER_ITEM)
    if kind == "actions":
        # a proposal, never a grant — whatever a card says
        return [{**item, "executable": False} for item in _items(value, ACTION_ITEM)]
    return None


def _public(meta: dict) -> dict | None:
    """One record as a reader gets it, or None when it is not a record of
    this contract: wrong version, unknown kind, no identity."""
    if meta.get("v") != VERSION or meta.get("kind") not in KINDS \
            or not isinstance(meta.get("id"), str) or not _ID_RE.match(meta["id"]):
        return None
    out = {}
    for key, kind in {**COMMON, **FIELDS[meta["kind"]]}.items():
        if key in meta:
            value = _read_field(kind, meta[key])
            if value is not None:
                out[key] = value
    if out.get("source") not in SOURCES:
        out["source"] = "agent-reported" if meta["kind"] == "response" else "observed"
    if meta["kind"] == "question" and not out.get("question"):
        return None
    if meta["kind"] in ("answer", "resolution") and not out.get("question"):
        return None
    return out


# ── reading ────────────────────────────────────────────────────────────


def _epoch(iso) -> float:
    try:
        return float(calendar.timegm(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return 0.0


def _ordered(found: list[dict]) -> list[dict]:
    """Oldest first: by when each says it was made, then file order — so a
    sync that interleaves two boards' lines still reads newest last."""
    return [r for _, _, r in sorted(
        (_epoch(r.get("at")), index, r) for index, r in enumerate(found))]


def _section(text: str) -> str:
    found = SECTION_RE.search(taskfiles.card_parts(text)["history"])
    return found.group(0) if found else ""


def parse(text: str) -> dict:
    """Every record in a card's Orchestration record section, as written
    (file order), plus counts of what was refused: lines that did not
    parse or were not records of this contract (`malformed`), and
    record-shaped lines anywhere else in the card (`misplaced`), which
    are never read as records."""
    section = _section(text)
    found, malformed = [], 0
    for match in RECORD_RE.finditer(section):
        try:
            meta = json.loads(match.group(1))
        except ValueError:
            malformed += 1
            continue
        public = _public(meta) if isinstance(meta, dict) else None
        if public is None:
            malformed += 1
            continue
        found.append(public)
    misplaced = len(RECORD_RE.findall(text)) - len(RECORD_RE.findall(section))
    return {"records": found, "malformed": malformed, "misplaced": max(misplaced, 0)}


def _raw_records(text: str) -> list[dict]:
    """The records as written, for a writer's identity checks."""
    out = []
    for match in RECORD_RE.finditer(_section(text)):
        try:
            meta = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(meta, dict) and _public(meta) is not None:
            out.append(meta)
    return out


def strip_records(text: str) -> str:
    """The card without what this module wrote: the section heading, its
    note, every record line and the readable line under each. Blank-line
    runs are folded, so the card reads as it would had nothing been
    recorded. Anything else in the section — a hand edit — stays."""
    out, skip_summary = [], False
    for line in text.splitlines():
        if skip_summary and SUMMARY_RE.match(line):
            skip_summary = False
            continue
        skip_summary = False
        stripped = line.strip()
        if re.match(rf"^##\s+{re.escape(HEADING)}\s*$", line) or stripped == NOTE:
            continue
        if RECORD_RE.match(line):
            skip_summary = True
            continue
        out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def card_rev(text: str) -> str:
    """A card's revision for orchestration: its text without the records
    the orchestration itself wrote, plus every decision a person made.
    So recording a response or opening a question does not make the
    response that opened it stale, while an owner's answer, a brief edit
    or any hand edit is a new revision."""
    people = sorted(r["id"] for r in parse(text)["records"]
                    if r.get("source") == "person")
    return hashlib.sha256((strip_records(text) + "\0" + ",".join(people))
                          .encode("utf-8")).hexdigest()[:16]


def _brief_rev(text: str) -> str:
    return taskfiles.brief_sha(taskfiles.card_parts(text)["brief"])[:16]


def read_text(text: str, filename: str) -> dict:
    """The whole reading of one card's orchestration history. Pure."""
    parsed = parse(text)
    records = _ordered(parsed["records"])
    brief_rev = _brief_rev(text)
    by_question: dict = {}
    for record in records:
        if record.get("question"):
            by_question.setdefault(record["question"], []).append(record)
    questions = []
    for qid, mine in by_question.items():
        asked = next((r for r in mine if r["kind"] == "question"), None)
        if asked is None:
            continue                   # an answer to nothing is history only
        newest = mine[-1]
        state, answer, resolution = "open", None, None
        if newest["kind"] == "answer":
            state, answer = "answered", newest
        elif newest["kind"] == "resolution" and newest.get("outcome") in (
                "withdrawn", "superseded"):
            state, resolution = newest["outcome"], newest
        questions.append({
            **{k: asked.get(k) for k in FIELDS["question"] if k in asked},
            "id": qid, "asked": asked.get("at"), "askedBy": asked.get("actor"),
            "source": asked.get("source"), "card": asked.get("card"),
            "state": state, "basis": newest["id"], "answer": answer,
            "resolution": resolution,
            # the brief moved since it was asked: the question itself must
            # be re-evaluated before anyone answers it
            "needsReevaluation": (state == "open" and bool(asked.get("briefRev"))
                                  and asked.get("briefRev") != brief_rev),
            "history": list(reversed(mine)),
        })
    order = {"open": 0, "answered": 1, "withdrawn": 2, "superseded": 3}
    questions.sort(key=lambda q: (order[q["state"]], -_epoch(q.get("asked"))))
    blockers: dict = {}
    for record in records:
        if record["kind"] == "blocker" and record.get("blocker"):
            blockers.setdefault(record["blocker"], []).append(record)
    blocker_list = [{**recs[0], "state": recs[-1].get("state") or "open",
                     "history": list(reversed(recs))} for recs in blockers.values()]
    counts = {s: sum(1 for q in questions if q["state"] == s) for s in QUESTION_STATES}
    return {
        "file": filename, "v": VERSION, "briefRev": brief_rev,
        "questions": questions, "counts": counts,
        "responses": [r for r in reversed(records) if r["kind"] == "response"],
        "blockers": blocker_list,
        "openBlockers": sum(1 for b in blocker_list if b["state"] == "open"),
        "attempts": [r for r in reversed(records) if r["kind"] == "attempt"],
        "actions": [r for r in reversed(records) if r["kind"] == "action"],
        "timeline": list(reversed(records)),
        "malformed": parsed["malformed"], "misplaced": parsed["misplaced"],
        "sources": {"observed": "Bench observed it",
                    "agent-reported": "a run reported it — a claim, not a check",
                    "person": "a person decided it"},
    }


def _card_path(filename: str, stage: str) -> Path:
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    if stage not in config.STAGE_DIRS:
        raise ValueError("unknown stage")
    path = config.TASKS / stage / filename
    if not path.is_file():
        raise ValueError(f"{filename} is not in {stage}/ — refresh the board")
    return path


def _summary(found: dict) -> dict | None:
    if not found["questions"] and not found["responses"] and not found["blockers"] \
            and not found["attempts"] and not found["actions"]:
        return None
    return {"open": found["counts"]["open"],
            "reevaluate": sum(1 for q in found["questions"] if q["needsReevaluation"]),
            "answered": found["counts"]["answered"],
            "openBlockers": found["openBlockers"],
            "responses": len(found["responses"])}


def for_card(filename: str, stage: str, board: dict | None = None) -> dict:
    """What a card's orchestration history shows — a GET. A phase card's
    reading also lists its members' questions, each linked to the member
    that owns it; an answer is given there, on the member's own card."""
    text = _card_path(filename, stage).read_text(encoding="utf-8")
    out = read_text(text, filename)
    # the brief an answer is bound to, as the answer route compares it
    out["briefSha"] = taskfiles.brief_sha(taskfiles.card_parts(text)["brief"])
    out.update(stage=stage, actor=actor(), aggregated=[],
               persistence={"commits": bool(config.COMMIT_MOVES),
                            "syncs": bool(getattr(config, "SYNC", False))})
    board = taskfiles.collect() if board is None else board
    tasks = {t["file"]: t for group in board["stages"] for t in group["tasks"]}
    task = tasks.get(filename) or {}
    if task.get("phase") and not task.get("isPhase"):
        out["phase"] = {"file": task["phase"]["file"],
                        "number": task["phase"].get("number")}
    for member in task.get("members") or [] if task.get("isPhase") else []:
        found = tasks.get(member["file"])
        if found is None:
            continue
        try:
            body = (config.TASKS / found["stage"] / found["file"]).read_text(
                encoding="utf-8")
        except OSError:
            continue
        for question in read_text(body, found["file"])["questions"]:
            out["aggregated"].append({
                **{k: question[k] for k in ("id", "text", "options", "recommendation",
                                            "state", "basis", "needsReevaluation",
                                            "asked", "answer") if k in question},
                "origin": {"file": found["file"], "stage": found["stage"],
                           "card": found.get("number")}})
    return out


def board(collected: dict) -> dict:
    """Per card, the few numbers a face needs, or nothing — a board with no
    orchestration history carries no key. A read of text already in hand."""
    out = {}
    for group in collected.get("stages") or []:
        for task in group.get("tasks") or []:
            body = task.get("body") or ""
            if "bench:orchestration" not in body:
                continue
            try:
                found = _summary(read_text(body, task["file"]))
            except (ValueError, KeyError):
                found = None
            if found:
                out[task["file"]] = found
    return out


# ── writing ────────────────────────────────────────────────────────────


def actor() -> dict:
    """Who a record made from this board is attributed to: the work
    state's git identity, else the operating-system user — said which."""
    name = taskfiles.actor_name()
    if name:
        return {"name": name, "source": "git"}
    try:
        return {"name": getpass.getuser(), "source": "os-user"}
    except (KeyError, OSError):
        return {"name": "", "source": "none"}


def _who() -> dict:
    found = actor()
    if not found["name"]:
        raise ValueError("a decision names who made it, and this board has no "
                         "identity — set `git config user.name` in the work-state "
                         "repository. Nothing was written.")
    return found


def _derived_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:10]


def _request_id(prefix: str, filename: str, request) -> str:
    """A person's record identity, from the page's request key: a double
    click, a resend or a replay finds the record it already made."""
    if not isinstance(request, str) or not _REQUEST_RE.match(request):
        raise ValueError("a request identity is required: 8–80 of letters, "
                         "digits, . _ : -")
    return _derived_id(prefix, filename, request)


def _readable(record: dict) -> str:
    """The line a person reads under a record. Built from the record, never
    read back; one line, angle brackets and controls removed, and it
    starts with `- <date>` so no reader takes it for a header, a heading,
    a marker or a criterion."""
    kind = record["kind"]
    words = {"observed": "observed", "agent-reported": "agent-reported",
             "person": record.get("actor") or "a person"}[record["source"]]
    if kind == "question":
        what = f"Question {record['question']} ({words})"
        body = record.get("text")
    elif kind == "answer":
        what = f"Answer to {record['question']} by {words}"
        body = record.get("choice") or record.get("note")
    elif kind == "resolution":
        what = f"Question {record['question']} {record.get('outcome')} ({words})"
        body = record.get("note")
    elif kind == "response":
        what = (f"Reasoning {record.get('operation')} ({record.get('need')}) "
                f"{record.get('status')} — agent-reported")
        body = record.get("summary") or ", ".join(record.get("diagnostics") or [])
    elif kind == "blocker":
        what = f"Blocker {record['blocker']} {record.get('state')} ({words})"
        body = record.get("text")
    elif kind == "attempt":
        what = (f"Recovery attempt {record.get('attempt')} of {record.get('of')} "
                f"for {record.get('incident')}: {record.get('outcome')} ({words})")
        body = record.get("summary")
    else:
        what = (f"Action {record.get('action')} {record.get('phase')}: "
                f"{record.get('outcome') or 'pending'} ({words})")
        body = record.get("summary")
    line = f"- {taskfiles.stamp(record['at'])} · {what}"
    if body:
        line += " — " + taskfiles.one_line(body, 200)
    return taskfiles.one_line(line, 400)


def _append(text: str, record: dict) -> str:
    """One record and its readable line, appended to the section, bounded."""
    # every angle bracket escaped, not only a comment's own delimiters: a
    # renderer that ever showed the line would show text, never markup
    body = (json.dumps(record, ensure_ascii=False, sort_keys=True)
            .replace("<", "\\u003c").replace(">", "\\u003e"))
    line = f"<!-- bench:orchestration {body} -->"
    if len(line.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ValueError(f"the {record['kind']} record is over "
                         f"{MAX_RECORD_BYTES // 1024} KiB — nothing was written")
    if len(_section(text).encode("utf-8")) + len(line) > MAX_SECTION_BYTES:
        raise ValueError(f"this card's orchestration record is at its "
                         f"{MAX_SECTION_BYTES // 1024} KiB bound — nothing was "
                         f"written; archive the card's history by hand first")
    return taskfiles.append_record(text, HEADING, NOTE, line + "\n" + _readable(record))


def _base(kind: str, record_id: str, source: str, who: dict, card: str | None,
          actor_kind: str) -> dict:
    out = {"v": VERSION, "id": record_id, "kind": kind, "source": source,
           "actor": who["name"], "actorSource": who["source"],
           "actorKind": actor_kind, "at": taskfiles.now_iso()}
    if card:
        out["card"] = card
    return out


def _number(filename: str) -> str | None:
    found = re.match(r"^(\d+)", filename)
    return taskfiles.canonical_number(found.group(1)) if found else None


def _state_of(text: str, question: str) -> tuple[dict | None, dict | None]:
    mine = _ordered([r for r in parse(text)["records"] if r.get("question") == question])
    asked = next((r for r in mine if r["kind"] == "question"), None)
    return asked, (mine[-1] if mine else None)


def _state_word(newest: dict | None) -> str:
    if newest is None or newest["kind"] == "question":
        return "open"
    if newest["kind"] == "answer":
        return "answered"
    return newest.get("outcome") or "open"


def answer(filename: str, stage: str, *, question: str, basis, brief_sha: str,
           actor_seen: str, choice=None, note=None, request=None) -> dict:
    """Record the owner's answer to one open question, on the card that
    owns it.

    Bound to everything the person was shown: the question's newest
    record (`basis`), the card's brief (`brief_sha`) and who the page said
    they are (`actor_seen`, which must still be this board's identity).
    The brief must also be the one the question was asked against —
    otherwise the question itself needs re-evaluation first. A question
    another person owns is refused (`Refused`). The same `request` again
    returns the record it already made."""
    _card_path(filename, stage)
    who = _who()
    if str(actor_seen or "") != who["name"]:
        raise taskfiles.StaleCard(
            f"this board's identity is {who['name']!r}, not the one your page "
            f"showed ({str(actor_seen or '')[:40]!r}) — nothing was recorded; "
            f"refresh and answer again")
    note = _clean("note", note)
    record_id = _request_id("d", filename, request)

    def change(text: str):
        known = {r.get("id"): r for r in _raw_records(text)}
        if record_id in known:
            return text, {"duplicate": True, "id": record_id,
                          "record": _public(known[record_id])}
        asked, newest = _state_of(text, question)
        if asked is None:
            raise ValueError(f"{filename} has no question {question!r}")
        state = _state_word(newest)
        if state != "open":
            raise taskfiles.StaleCard(
                f"question {question} is {state} — nothing was recorded; read "
                f"its history")
        if (basis or None) != newest["id"]:
            raise taskfiles.StaleCard(
                f"question {question} changed since you read it — nothing was "
                f"recorded; read it again and answer")
        brief = taskfiles.card_parts(text)["brief"]
        if taskfiles.brief_sha(brief) != brief_sha:
            raise taskfiles.StaleCard(
                f"the brief of {filename} changed since you read it — nothing "
                f"was recorded; read it again and answer")
        if asked.get("briefRev") and asked["briefRev"] != _brief_rev(text):
            raise taskfiles.StaleCard(
                f"the brief changed after question {question} was asked, so the "
                f"question needs re-evaluation before it is answered — nothing "
                f"was recorded")
        owner = asked.get("owner")
        if owner and owner != who["name"]:
            raise Refused(f"question {question} is addressed to {owner}; "
                          f"{who['name']} may read it but not answer it")
        options = asked.get("options") or []
        picked, index = None, None
        if choice is not None and choice != "":
            if isinstance(choice, bool) or not isinstance(choice, int) \
                    or not 0 <= choice < len(options):
                raise ValueError(f"choice must be the number of one of the "
                                 f"{len(options)} options")
            index, picked = choice, options[choice]
        if picked is None and len(note) < 3:
            raise ValueError("an answer picks one of the options or says what "
                             "was decided in a note")
        record = _base("answer", record_id, "person", who, _number(filename), "person")
        record.update(question=question, basis=newest["id"],
                      briefRev=_brief_rev(text), supersedes=newest["id"])
        if asked.get("scope"):
            record["scope"] = asked["scope"]
        if picked is not None:
            record.update(choice=picked, choiceIndex=index)
        if note:
            record["note"] = note
        return _append(text, record), {"duplicate": False, "id": record_id,
                                       "record": _public(record)}

    return taskfiles.edit_card(filename, stage, change, f"answer to {question}")


def withdraw(filename: str, stage: str, *, question: str, basis, note,
             request=None) -> dict:
    """A person withdraws an open question — it no longer needs an answer.
    The question, its options and everything about it stay readable."""
    _card_path(filename, stage)
    who = _who()
    note = _clean("note", note, required=True)
    record_id = _request_id("w", filename, request)

    def change(text: str):
        known = {r.get("id"): r for r in _raw_records(text)}
        if record_id in known:
            return text, {"duplicate": True, "id": record_id,
                          "record": _public(known[record_id])}
        asked, newest = _state_of(text, question)
        if asked is None:
            raise ValueError(f"{filename} has no question {question!r}")
        if _state_word(newest) != "open":
            raise taskfiles.StaleCard(f"question {question} is "
                                      f"{_state_word(newest)} — nothing was recorded")
        if (basis or None) != newest["id"]:
            raise taskfiles.StaleCard(f"question {question} changed since you read "
                                      f"it — nothing was recorded")
        owner = asked.get("owner")
        if owner and owner != who["name"]:
            raise Refused(f"question {question} is addressed to {owner}")
        record = _base("resolution", record_id, "person", who, _number(filename),
                       "person")
        record.update(question=question, outcome="withdrawn", note=note,
                      basis=newest["id"], supersedes=newest["id"])
        return _append(text, record), {"duplicate": False, "id": record_id,
                                       "record": _public(record)}

    return taskfiles.edit_card(filename, stage, change, f"question {question} withdrawn")


def note(filename: str, stage: str, kind: str, fields: dict, *, key: str,
         source: str = "observed", actor_kind: str = "orchestrator") -> dict:
    """An observed fact on the card — a blocker opened or cleared, a
    recovery attempt and its outcome, an action's intent or receipt. For
    the orchestration engine's own bookkeeping (later members ask it);
    `key` is the caller's idempotency key, so recording the same fact
    twice — a restart replaying a receipt — writes it once."""
    if kind not in ("blocker", "attempt", "action"):
        raise ValueError(f"{kind!r} is not a fact this records")
    if source not in ("observed", "agent-reported"):
        raise ValueError("a recorded fact is observed or agent-reported")
    if not isinstance(key, str) or not _REQUEST_RE.match(key):
        raise ValueError("an idempotency key is 8–80 of letters, digits, . _ : -")
    _card_path(filename, stage)
    who = _who()
    record_id = _derived_id({"blocker": "b", "attempt": "t", "action": "x"}[kind],
                            filename, key)
    extra = dict(fields or {})
    unknown = set(extra) - set(FIELDS[kind])
    if unknown:
        raise ValueError(f"unknown field(s) for a {kind}: {', '.join(sorted(unknown))}")
    record = _base(kind, record_id, source, who, _number(filename), actor_kind)
    for name, value in extra.items():
        shape = FIELDS[kind][name]
        if shape == "text":
            record[name] = _clean(name, value)
        elif shape == "short":
            record[name] = _clean(name, value, MAX_SHORT)
        elif shape == "word":
            if not _word(value):
                raise ValueError(f"'{name}' must be a short lowercase word")
            record[name] = value
        elif shape == "int":
            if _int(value) is None:
                raise ValueError(f"'{name}' must be a small whole number")
            record[name] = value
        elif shape == "refs":
            record[name] = _refs(name, value)
        elif shape == "id":
            if not isinstance(value, str) or not _ID_RE.match(value):
                raise ValueError(f"'{name}' must be a record identity")
            record[name] = value
    if kind == "blocker":
        record.setdefault("blocker", _derived_id("b", filename, "blocker", key))
        record.setdefault("state", "open")
        if record["state"] not in ("open", "cleared"):
            raise ValueError("a blocker is open or cleared")
    if kind == "action":
        record.setdefault("phase", "intent")
        if record["phase"] not in ("intent", "receipt"):
            raise ValueError("an action record is its intent or its receipt")

    def change(text: str):
        if any(r.get("id") == record_id for r in _raw_records(text)):
            return text, {"duplicate": True, "id": record_id}
        return _append(text, record), {"duplicate": False, "id": record_id,
                                       "record": _public(record)}

    return taskfiles.edit_card(filename, stage, change, f"orchestration {kind}")


def record_response(job: dict, board: dict | None = None) -> dict:
    """Put a settled reasoning job's public outcome on its card: the
    validated response (summary, findings, proposed actions, blockers) for
    `valid` and `stale`, the refusal's codes for `invalid`, and the ending
    for `failed` and `cancelled` — never raw text, never private
    reasoning. A `valid` response's questions are opened on the card,
    addressed to the person whose board asked; the open questions an
    earlier response to the same need opened are resolved as superseded,
    their text and options kept. Idempotent: the job's identity is the
    record's, so settling twice writes once."""
    filename = job["task"]
    stage = taskfiles.find_stage_of(filename)
    if stage is None:
        raise ValueError(f"{filename} is not on the board")
    status = job.get("status")
    if status not in ("valid", "stale", "invalid", "failed", "cancelled"):
        raise ValueError(f"a {status} reasoning job has no outcome to record")
    who = {"name": str(job.get("actor") or "unattributed")[:MAX_SHORT],
           "source": "job"}
    actor_kind = "orchestrator" if job.get("via") == "orchestrator" else "person"
    card = str(job.get("card") or _number(filename) or "")
    response = job.get("response") or {}
    record_id = _derived_id("o", filename, job["id"])
    record = _base("response", record_id, "agent-reported", who, card, actor_kind)
    record.update(operation=str(job["id"])[:MAX_SHORT], need=job.get("need"),
                  status=status, via=job.get("via"),
                  briefRev=str((job.get("binding") or {}).get("briefRev") or "")[:16])
    for key in ("model", "harness"):
        if job.get(key):
            record[key] = str(job[key])[:MAX_SHORT]
    if job.get("name"):
        record["agent"] = str(job["name"])[:MAX_SHORT]
    if job.get("scope"):
        record["scope"] = str(job["scope"])[:MAX_SHORT]
    if status in ("valid", "stale"):
        record["summary"] = _clean("summary", response.get("summary"))
        record["findings"] = [
            {"id": f.get("id"), "statement": _clean("statement", f.get("statement")),
             "severity": f.get("severity"), "evidence": _refs("evidence", f.get("evidence"))}
            for f in (response.get("findings") or [])[:MAX_ITEMS]]
        record["blockers"] = [
            {"id": b.get("id"), "blocker": _clean("blocker", b.get("blocker")),
             "evidence": _refs("evidence", b.get("evidence"))}
            for b in (response.get("blockers") or [])[:MAX_ITEMS]]
        record["actions"] = [
            {"id": a.get("id"), "action": a.get("action"), "card": str(a.get("card")),
             "why": _clean("why", a.get("why")), "evidence": _refs("evidence", a.get("evidence")),
             "executable": False}
            for a in (response.get("actions") or [])[:MAX_ITEMS]]
    else:
        record["diagnostics"] = sorted({d.get("code") for d in job.get("diagnostics") or []
                                        if _word(d.get("code"))})[:MAX_ITEMS]
        if status in ("failed", "cancelled"):
            record["summary"] = _clean("summary", str(job.get("error") or "")[:MAX_TEXT])
    opened = []
    if status == "valid":
        for item in (response.get("questions") or [])[:MAX_ITEMS]:
            qid = _derived_id("q", filename, job["id"], str(item.get("id")))
            question = _base("question", qid, "agent-reported", who, card, actor_kind)
            question.update(
                question=qid, text=_clean("question", item.get("question")),
                options=[_clean("option", o) for o in (item.get("options") or [])
                         [:MAX_OPTIONS]],
                recommendation=_clean("recommendation", item.get("recommendation")),
                why=_clean("why", item.get("why")),
                evidence=_refs("evidence", item.get("evidence")),
                operation=record["operation"],
                need=job.get("need"), item=_word(item.get("id")) or None,
                briefRev=record["briefRev"])
            if record.get("scope"):
                question["scope"] = record["scope"]
            if who["name"] and who["name"] != "unattributed":
                # addressed to the person whose board asked; a board with
                # no identity asks questions anyone on the card may answer
                question["owner"] = who["name"]
            opened.append(question)
        record["questions"] = [q["id"] for q in opened]
    tasks = {t["file"]: t for g in (board or taskfiles.collect())["stages"]
             for t in g["tasks"]}
    phase = (tasks.get(filename) or {}).get("phase")
    for question in opened:
        if phase and phase.get("number"):
            question["phase"] = str(phase["number"])

    def change(text: str):
        if any(r.get("id") == record_id for r in _raw_records(text)):
            return text, {"duplicate": True, "id": record_id}
        updated = _append(text, record)
        superseded = []
        if status == "valid":
            current = _ordered(parse(text)["records"])
            for asked in [r for r in current if r["kind"] == "question"
                          and r.get("need") == job.get("need")
                          and r.get("source") == "agent-reported"]:
                _, newest = _state_of(text, asked["question"])
                if _state_word(newest) != "open":
                    continue
                gone = _base("resolution", _derived_id("s", filename, asked["question"],
                                                       job["id"]),
                             "observed", who, card, actor_kind)
                gone.update(question=asked["question"], outcome="superseded",
                            by=record["operation"], basis=newest["id"],
                            supersedes=newest["id"],
                            note="a newer response to the same need replaced it")
                updated = _append(updated, gone)
                superseded.append(asked["question"])
            for question in opened:
                updated = _append(updated, question)
        return updated, {"duplicate": False, "id": record_id,
                         "opened": [q["id"] for q in opened], "superseded": superseded}

    try:
        return taskfiles.edit_card(filename, stage, change,
                                   f"reasoning {job['id']} {status}")
    except taskfiles.StaleCard:
        # a write landed between the read and the replace: read again once
        return taskfiles.edit_card(filename, stage, change,
                                   f"reasoning {job['id']} {status}")
