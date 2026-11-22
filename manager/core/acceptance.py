"""Acceptance records and review findings: what a card's requirements have
been *judged* against, and what its reviews found and what became of it
(task 81).

A card's brief says what must be true; this module keeps the record of
whether it is. Three things it will not do, because each would let a
number on the card say more than anyone established:

- **Nothing ticks itself.** A criterion reads `accepted` only through a
  decision a named person made — either confirming it outright, with a
  note, or mapping one of bench's own passing evidence records to it. An
  agent's report, a clean exit, an unrelated green suite, a merge or a
  move to `done/` changes nothing here. What an agent *can* do is say it
  implemented a criterion (`bench-progress --criterion`), which reads as
  `reported — awaiting confirmation`, a claim beside the run that made it.
- **Viewing writes nothing.** Reading a card computes every state from the
  file, bench's evidence records and the runs' progress; the only writes
  are the explicit operations below, each validated against what the
  person was shown (the brief's revision, the criterion's revision, the
  newest decision they saw) so a stale page is refused rather than
  allowed to overwrite a newer decision.
- **History is never rewritten.** Records are appended, one per line,
  under the card's `## Acceptance record` and `## Review findings`
  sections — the history half of `taskfiles.card_parts`, which a
  refinement never touches. The newest record about a thing is its state;
  every earlier one stays readable.

Storage extends #65's version-1 contract (AGENTS.md, "The persistence
contract"): criterion ids are `<!-- ac:<id> -->` on the criterion's own
line, assigned only by `track`; acceptance records are
`<!-- bench:acceptance {...} -->`; finding events are
`<!-- bench:finding {...} -->`. Every record carries `"v": 1`.
"""

from __future__ import annotations

import calendar
import getpass
import hashlib
import json
import re
import secrets
import subprocess
import time
from pathlib import Path

import config
import evidence
import progress
import staleness
import taskfiles

VERSION = 1

# What a criterion's state *licenses a reader to believe*, in words. The
# word carries the meaning; colour never stands alone.
#
# Task 190 put the outcome first. The stored keys are the ones #81 wrote
# (`needs-work`, `reported`), so every record on every card reads as it
# always did; only the words changed, and two states were added for what
# a review can say that a person's form never did.
STATES = {
    "accepted": "Accepted",
    "needs-work": "Not met",
    "not-verified": "Not verified",
    "decision": "Needs your decision",
    "reported": "Met — awaiting review",
    "recheck": "Needs recheck",
    "pending": "No result yet",
}
# The order the aggregate is read in: what needs someone first, what is
# settled last.
STATE_ORDER = ("needs-work", "decision", "recheck", "not-verified", "reported",
               "pending", "accepted")

# What a run may say about one criterion in its closing report, by the
# role it ran in (task 190). An implementer says whether it met a
# criterion; only the independent reviewer can say *accepted*. Each word
# maps to the state a reader is shown.
RESULT_WORDS = {
    "implementer": {"met": "reported", "not-met": "needs-work",
                    "not-verified": "not-verified", "decision": "decision"},
    "reviewer": {"accepted": "accepted", "not-met": "needs-work",
                 "not-verified": "not-verified", "decision": "decision"},
}
ROLE_OF_INTENT = {"work": "implementer", "act-pr": "implementer",
                  "review": "reviewer"}
MAX_RESULTS = 60
MAX_RESULT_REASON = 400
MAX_PROBLEMS = 12
RESULTS_HEADER_RE = re.compile(r"^\s*(?:#+\s*)?\**\s*ACCEPTANCE RESULTS\s*\**\s*:?\s*$",
                               re.MULTILINE | re.IGNORECASE)
_RESULT_LINE_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?`?\[?([0-9a-f]{12})\]?`?\s*[:—–-]\s*"
    r"(met|not[ -]met|not[ -]verified|accepted|needs(?: your)?[ -]decision|decision)"
    r"\s*(?:[—–:-]\s*(.*?))?\s*$", re.IGNORECASE)
_RESULT_EVIDENCE_RE = re.compile(r"\[\s*evidence\s*:\s*([^\]]*)\]", re.IGNORECASE)
_CID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
# What a person may record about a criterion. `reported` is not here: it
# is an agent's claim (read from the progress channel) or a legacy tick
# carried over by tracking — never a decision somebody makes in this form.
DECISIONS = ("accepted", "needs-work", "recheck", "pending")

FINDING_STATES = {
    "open": "open",
    "fix-reported": "fix reported — awaiting recheck",
    "resolved": "resolved",
}
FINDING_EVENTS = ("opened", "fix-reported", "resolved", "reopened")

MAX_NOTE = 2000
MAX_REF = 300
MAX_FINDING = 2000
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f‎‏‪-‮⁦-⁩]")
_REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

# `## PR review at 0123abcd4567 (superseded — …) — 2026-11-08 15:20 (Juno)`
# — the heading agents.py files a review under. Its identity is the line.
ROUND_RE = re.compile(
    r"^##\s+(PR review(?: at ([0-9a-f]{7,40}))?(?:\s+\((historical|superseded)[^)]*\))?)"
    r"\s+[—–-]\s+(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)(?:\s+\(([^)]*)\))?\s*$",
    re.MULTILINE)
REPORT_RE = re.compile(
    r"^##\s+(.+?)\s+[—–-]\s+(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)(?:\s+\(([^)]*)\))?\s*$",
    re.MULTILINE)
VERDICT_RE = re.compile(r"^PR REVIEW:\s*(APPROVE|REQUEST CHANGES)", re.MULTILINE)
# `## Relevance review — 2026-11-08 08:11 (Hazel)` — the heading agents.py
# files a relevance check under (task 84). Older boards wrote the date
# alone, and sometimes no reviewer; those are read as legacy, never
# completed with a time or a name nobody recorded.
RELEVANCE_RE = re.compile(
    r"^##\s+Relevance review\s+[—–-]\s+(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?"
    r"(?:\s+\(([^)]*)\))?\s*$")
RELEVANCE_VERDICT_RE = re.compile(r"^\**\s*RELEVANCE REVIEW:\**\s*(.+?)\s*$", re.MULTILINE)
# The verdict words the relevance prompt asks for, read off the start of
# the marker line. Anything else stays the reviewer's own words.
RELEVANCE_WORDS = ("Still relevant", "Partly done", "Already done", "Needs rewrite")

# What bench itself recorded about a review when it filed it (task 168):
# one line directly under the report's heading, written by the board and
# never by the run — a run's line shaped like it is quoted on the way in
# (`reports.defuse`). It names what the review was *of*: for a relevance
# review the brief revision the run was launched with, for a PR review the
# full head and base it was pinned to and who said so. A report filed
# before task 168 has none, and every field it would hold reads unknown.
PROVENANCE_RE = re.compile(r"^<!-- bench:review (\{.*\}) -->[ \t]*$")
PROVENANCE_VERSION = 1
_SHA_FULL_RE = re.compile(r"^[0-9a-f]{7,64}$")
_PROV_TEXT = 300


# ── reading ────────────────────────────────────────────────────────────


def criterion_rev(text: str) -> str:
    """A criterion's revision: what a decision is bound to. Whitespace is
    not wording, so re-wrapping a line does not invalidate anything."""
    return hashlib.sha256(" ".join(str(text).split()).encode("utf-8")).hexdigest()[:12]


def label(text: str, cap: int = 90) -> str:
    """A concise readable label; the complete requirement travels with it."""
    clean = re.sub(r"[*_`]", "", " ".join(str(text).split()))
    if len(clean) <= cap:
        return clean
    cut = clean[:cap].rsplit(" ", 1)[0]
    return cut.rstrip(",;:.—-") + "…"


def _epoch(iso) -> float:
    try:
        return float(calendar.timegm(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return 0.0


def _ordered(records: list[dict]) -> list[dict]:
    """Oldest first: by when the record says it was made, then file order —
    so a sync that interleaves two boards' lines still reads newest last."""
    return [r for _, _, r in sorted(
        (_epoch(r.get("at")), index, r) for index, r in enumerate(records))]


def _legacy_ticks(brief: str) -> dict:
    """Which criteria carry a hand-written `[x]` — by position in the
    checklist, the only identity an untracked criterion has."""
    out, index, fenced = {}, 0, False
    for line in brief.splitlines():
        if taskfiles.is_fence(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = re.match(r"^\s*[-*+]\s+\[([ xX])\]\s+", line)
        if match:
            out[index] = match.group(1).lower() == "x"
            index += 1
    return out


def _evidence_now(evidence_id: str, filename: str) -> dict:
    record = evidence.read(evidence_id) if evidence.is_evidence_id(evidence_id) else None
    if record is None or record.get("task") != filename:
        return {"id": evidence_id, "found": False, "outcome": "missing"}
    now = staleness.outcome_now(record)
    source = record.get("source") or {}
    return {"id": evidence_id, "found": True, "check": record.get("check"),
            "scope": record.get("scope"), "outcome": now.get("outcome"),
            "reason": now.get("reason"), "why": now.get("why"),
            "commit": (source.get("commit") or "")[:12] or None,
            "ended": record.get("ended")}


def _same_commit(a, b) -> bool:
    a, b = str(a or ""), str(b or "")
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def agent_results(text: str) -> tuple[list[dict], list[dict]]:
    """Every per-criterion result a run reported, read off the provenance
    bench wrote under each filed report (task 190) — oldest first — and,
    per report, what its reporting amounted to (how many results, what was
    not understood, how many criteria it left without one). Pure: the
    card's own text is the record, so a reload, a restart or a sync reads
    the same results and nothing is written to read them."""
    history = taskfiles.card_parts(text)["history"]
    heads = list(REPORT_RE.finditer(history))
    events, reporting = [], []
    for index, head in enumerate(heads):
        stop = heads[index + 1].start() if index + 1 < len(heads) else len(history)
        prov, _body = split_provenance(_report_body(history, head.end(), stop))
        acc = (prov or {}).get("acceptance")
        if not acc:
            continue
        line = head.group(0).strip()
        kind = head.group(1).strip()
        who = head.group(3) or (prov or {}).get("reviewer") or ""
        at = (prov or {}).get("filed") or ""
        commit = acc.get("head") or ((prov or {}).get("head") or {}).get("commit")
        reporting.append({"kind": kind, "at": head.group(2), "filed": at, "by": who,
                          "role": acc["role"], "commit": (commit or "")[:12] or None,
                          "reported": acc["reported"], "count": len(acc["results"]),
                          "missing": acc.get("missing", 0),
                          "problems": acc.get("problems") or [],
                          "agent": (prov or {}).get("agent"),
                          "model": (prov or {}).get("model")})
        for result in acc["results"]:
            events.append({
                "id": "ar-" + hashlib.sha256(
                    f"{line}|{result['rev']}".encode("utf-8")).hexdigest()[:10],
                "source": "agent", "role": acc["role"], "result": result["result"],
                "state": RESULT_WORDS[acc["role"]][result["result"]],
                "criterion": result.get("criterion"), "rev": result["rev"],
                "reason": result.get("reason") or "", "evidenceIds": result.get("evidence") or [],
                "commit": (commit or "")[:12] or None, "at": at,
                "actor": who, "agent": (prov or {}).get("agent"),
                "model": (prov or {}).get("model"), "report": kind,
                "reportAt": head.group(2)})
    return _ordered(events), reporting


def _for_item(item: dict, events: list[dict]) -> list[dict]:
    """The agent results about one current criterion: by its id where the
    result named one, else by the revision it was reported against — so a
    result filed before the criterion had an id still finds it, and a
    reworded criterion's old results stay with its id, to be rechecked."""
    out = []
    for event in events:
        if item.get("id") and event.get("criterion"):
            if event["criterion"] == item["id"]:
                out.append(event)
        elif event["rev"] == item["rev"]:
            out.append(event)
    return out


def _effective(item: dict, records: list[dict], claim: dict | None,
               filename: str, results: list[dict] | None = None,
               head: str | None = None) -> dict:
    """One criterion's state, computed — never stored.

    Two kinds of record are read in time order: a person's decisions
    (`## Acceptance record`) and the results runs reported in their filed
    reports (task 190). The newest stands, with two exceptions that keep a
    report from overwriting a judgment silently: a run's result never
    supersedes a person's decision about the same commit, and an
    implementer's "met" never supersedes the reviewer's result about the
    same commit. Then the newest is held to what it was about: the
    requirement reworded (its revision differs), the record marked for
    recheck by an applied refinement, the evidence it rested on no longer
    reading `passed`, or — for a run's result — the code it assessed no
    longer being the candidate. Each of those is `recheck` with the
    reason, and the earlier result is kept and shown as history."""
    timeline = _ordered(list(records) + list(results or []))
    current, skipped = None, []
    for event in timeline:
        if current is not None and event.get("source") in ("tracked", "legacy-tick"):
            # giving a criterion its id is bookkeeping, not a result: it
            # never sets an earlier result aside
            continue
        if current is not None and event.get("source") == "agent":
            if current.get("source") != "agent" and current.get("state") != "pending" \
                    and _same_commit(current.get("commit"), event.get("commit")):
                skipped.append(event)
                continue
            if current.get("source") == "agent" and current.get("role") == "reviewer" \
                    and event.get("role") == "implementer" \
                    and _same_commit(current.get("commit"), event.get("commit")):
                skipped.append(event)
                continue
        current = event
    if current is not None and current.get("source") == "agent":
        return _from_result(item, current, claim, filename, head, skipped)
    out = _from_decision(item, current, claim, filename)
    out["skipped"] = skipped
    return out


def _from_result(item: dict, result: dict, claim: dict | None, filename: str,
                 head: str | None, skipped: list) -> dict:
    who = "reviewer" if result["role"] == "reviewer" else "implementation agent"
    out = {"state": result["state"], "reason": result.get("reason") or None,
           "decision": None, "result": result, "evidence": None,
           "evidenceList": [_evidence_now(e, filename) for e in result["evidenceIds"]],
           "claim": claim, "by": result["role"], "skipped": skipped}
    if result["rev"] != item["rev"]:
        out.update(state="recheck",
                   reason=f"the requirement was reworded after the {who} reported "
                          f"“{result['result'].replace('-', ' ')}”")
    elif head and result.get("commit") and not _same_commit(head, result["commit"]):
        out.update(state="recheck",
                   reason=f"the code changed since the {who} assessed it at "
                          f"{result['commit']}; this result is about that commit")
    elif result["state"] == "accepted":
        bad = [e for e in out["evidenceList"] if e.get("outcome") != "passed"]
        if bad:
            out.update(state="recheck",
                       reason=f"evidence {bad[0]['id']} it rested on now reads "
                              f"{bad[0].get('outcome')}")
    return out


def _from_decision(item: dict, decision: dict | None, claim: dict | None,
                   filename: str) -> dict:
    """A person's newest decision about a criterion — #81's reading, with
    the words task 190 gave the states."""
    out = {"state": "pending", "reason": None, "decision": decision, "result": None,
           "evidence": None, "evidenceList": [], "claim": claim,
           "by": "person" if decision else None}
    claim_at = (claim or {}).get("at") or 0
    if decision is None:
        if claim:
            out.update(state="reported", by="implementer",
                       reason="an agent reported it implemented while working — "
                              "its filed report will say more")
        return out
    if decision.get("source") in ("tracked", "legacy-tick"):
        out["by"] = None
    state = decision.get("state")
    decided_at = _epoch(decision.get("at"))
    if decision.get("evidence"):
        out["evidence"] = _evidence_now(str(decision["evidence"]), filename)
    if decision.get("recheck"):
        out.update(state="recheck",
                   reason=decision.get("recheckReason") or "marked for recheck")
    elif decision.get("rev") and decision["rev"] != item["rev"]:
        out.update(state="recheck",
                   reason="the requirement was reworded after this decision")
    elif state == "accepted":
        ev = out["evidence"]
        if ev is not None and ev.get("outcome") != "passed":
            out.update(state="recheck",
                       reason=f"the supporting evidence now reads "
                              f"{ev.get('outcome')}" +
                              (f" — {ev['why']}" if ev.get("why") else ""))
        else:
            out.update(state="accepted", reason=decision.get("note") or
                       (f"bench evidence {ev['id']} reads passed" if ev else None))
    elif state == "needs-work":
        if claim and claim_at > decided_at:
            out.update(state="reported", by="implementer",
                       reason="a fix was reported after it was recorded as not met")
        else:
            out.update(state="needs-work", reason=decision.get("note"))
    elif state == "recheck":
        out.update(state="recheck", reason=decision.get("note") or "recheck requested")
    elif state == "reported":
        # A tick written by hand is a claim nobody verified (task 190): it
        # reads *Not verified*, never *Met* and never *Accepted*.
        if decision.get("source") == "legacy-tick":
            out.update(state="not-verified", by=None,
                       reason="ticked by hand in the brief — an unverified claim, "
                              "not a result")
        else:
            out.update(state="reported", reason="reported")
    else:
        if claim and claim_at > decided_at:
            out.update(state="reported", reason="an agent reported it implemented")
    return out


def _prov_text(value, cap: int = _PROV_TEXT):
    if not isinstance(value, str):
        return None
    value = " ".join(_CONTROL.sub("", value).split())
    return value[:cap] or None


def _prov_ref(value) -> dict | None:
    """A head or base as the marker keeps it: the ref, the full commit and
    who said so (`github`, `local`, …) — each field only when it reads as
    what it claims to be."""
    if not isinstance(value, dict):
        return None
    commit = value.get("commit")
    out = {"ref": _prov_text(value.get("ref"), 200),
           "commit": commit if isinstance(commit, str) and _SHA_FULL_RE.match(commit) else None,
           "authority": _prov_text(value.get("authority"), 40)}
    return out if any(out.values()) else None


PROVENANCE_TEXT = ("kind", "agent", "model", "reviewer", "filed", "label", "pr",
                   "prState", "note", "published", "briefSource")


def read_provenance(raw: str) -> dict | None:
    """One provenance marker's JSON, read field by field: only the fields
    version 1 names, each bounded and checked, so a hand-edited or newer
    marker can neither inject markup nor claim a shape bench did not
    write. Anything that does not parse is None — unknown, never guessed."""
    try:
        meta = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(meta, dict) or meta.get("v") != PROVENANCE_VERSION:
        return None
    out: dict = {"v": PROVENANCE_VERSION}
    for key in PROVENANCE_TEXT:
        value = _prov_text(meta.get(key))
        if value:
            out[key] = value
    for key in ("briefRevision", "reportSha"):
        value = meta.get(key)
        if isinstance(value, str) and _SHA_FULL_RE.match(value):
            out[key] = value
    number = meta.get("prNumber")
    if isinstance(number, (int, str)) and str(number).isdigit():
        out["prNumber"] = str(number)
    for key in ("head", "base"):
        ref = _prov_ref(meta.get(key))
        if ref:
            out[key] = ref
    found = meta.get("evidence")
    if isinstance(found, list):
        out["evidence"] = [e for e in found[:20]
                           if isinstance(e, str) and evidence.is_evidence_id(e)]
    results = read_results(meta.get("acceptance"))
    if results is not None:
        out["acceptance"] = results
    return out


def read_results(raw) -> dict | None:
    """The per-criterion results a filed report's provenance carries (task
    190), read field by field like the rest of the marker: a role this
    version names, each result a revision bench gave the run and a word
    that role may say, bounded text, evidence ids by shape. Anything else
    is dropped — never guessed into a result."""
    if not isinstance(raw, dict) or raw.get("v") != VERSION:
        return None
    role = raw.get("role")
    if role not in RESULT_WORDS:
        return None
    head = raw.get("head")
    out = {"v": VERSION, "role": role,
           "head": head if isinstance(head, str) and _SHA_FULL_RE.match(head) else None,
           "reported": bool(raw.get("reported")), "results": [], "problems": [],
           "missing": raw.get("missing") if isinstance(raw.get("missing"), int)
           and 0 <= raw.get("missing") < 1000 else 0}
    for item in (raw.get("results") or [])[:MAX_RESULTS] \
            if isinstance(raw.get("results"), list) else []:
        if not isinstance(item, dict):
            continue
        rev, word = item.get("rev"), item.get("result")
        if not (isinstance(rev, str) and re.fullmatch(r"[0-9a-f]{12}", rev)) \
                or word not in RESULT_WORDS[role]:
            continue
        cid = item.get("criterion")
        found = item.get("evidence")
        out["results"].append({
            "rev": rev, "result": word,
            "criterion": cid if isinstance(cid, str) and _CID_RE.match(cid) else None,
            "reason": _prov_text(item.get("reason"), MAX_RESULT_REASON) or "",
            "evidence": [e for e in found[:10] if evidence.is_evidence_id(e)]
            if isinstance(found, list) else []})
    if isinstance(raw.get("problems"), list):
        out["problems"] = [p for p in (_prov_text(x, 200) for x in
                                       raw["problems"][:MAX_PROBLEMS]) if p]
    return out


def parse_results(text: str) -> tuple[list[dict], list[str], bool]:
    """A run's `ACCEPTANCE RESULTS` block, read off its own output: the
    last such block, one line per criterion — the criterion's revision as
    the prompt gave it, the result word, a short reason, and optionally
    `[evidence: <id>, …]`. Returns the lines read, the lines that were not
    understood (each said, never silently dropped) and whether a block was
    there at all. Pure: it judges nothing against the card."""
    heads = list(RESULTS_HEADER_RE.finditer(text or ""))
    if not heads:
        return [], [], False
    lines = text[heads[-1].end():].split("\n")
    items, problems, started = [], [], False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if started:
                break
            continue
        if stripped.startswith("#") or re.match(
                r"^(PR REVIEW|ADDRESSED|NOT READY|RELEVANCE REVIEW)\s*:", stripped):
            break
        started = True
        match = _RESULT_LINE_RE.match(stripped)
        if not match:
            problems.append(f"not understood: {stripped[:120]}")
            continue
        word = re.sub(r"[ -]+", "-", match.group(2).lower())
        word = {"needs-decision": "decision", "needs-your-decision": "decision"}.get(word, word)
        reason = match.group(3) or ""
        ev = []
        found = _RESULT_EVIDENCE_RE.search(reason)
        if found:
            ev = [e.strip() for e in found.group(1).split(",") if e.strip()]
            reason = (reason[:found.start()] + reason[found.end():]).strip(" —–-")
        items.append({"rev": match.group(1).lower(), "result": word,
                      "reason": " ".join(reason.split()), "evidence": ev})
        if len(items) >= MAX_RESULTS:
            break
    return items, problems[:MAX_PROBLEMS], True


def filing_results(output: str, card_text: str, filename: str, role: str,
                   head: str | None) -> dict:
    """What a run's closing report says about each criterion, validated
    against the card it is filed on, ready to travel in the report's
    board-written provenance (task 190). The run never writes this line:
    bench reads the run's words, keeps only what binds — a revision of a
    criterion on this card now, a word the run's role may say, a reason, and
    evidence bench made for this card — and says what it dropped and which
    criteria the run left without a result. Partial is reported as
    partial; nothing is ever completed in the run's favour."""
    words = RESULT_WORDS[role]
    items, problems, present = parse_results(output)
    brief = taskfiles.card_parts(card_text)["brief"]
    current = {}
    for item in taskfiles.criteria(brief):
        current.setdefault(criterion_rev(item["text"]), item)
    kept: dict = {}
    for item in items:
        crit = current.get(item["rev"])
        if crit is None:
            problems.append(f"{item['rev']}: no criterion on this card has that "
                            f"revision — the requirement may have changed")
            continue
        if item["result"] not in words:
            allowed = ", ".join(w.replace("-", " ") for w in words)
            problems.append(f"{item['rev']}: '{item['result']}' is not a result a "
                            f"{role} records (one of {allowed})")
            continue
        if item["result"] == "decision" and len(item["reason"]) < 3:
            problems.append(f"{item['rev']}: a decision names the question to "
                            f"decide; none was given")
            continue
        ev, dropped = [], []
        for eid in item["evidence"][:10]:
            record = evidence.read(eid) if evidence.is_evidence_id(eid) else None
            (ev if record is not None and record.get("task") == filename
             else dropped).append(eid)
        if dropped:
            problems.append(f"{item['rev']}: evidence {', '.join(dropped)[:80]} is not "
                            f"a record bench made for this card — left out")
        if item["rev"] in kept:
            problems.append(f"{item['rev']}: reported twice — the last line counts")
        kept[item["rev"]] = {"rev": item["rev"], "criterion": crit.get("id"),
                             "result": item["result"],
                             "reason": _prov_text(item["reason"], MAX_RESULT_REASON) or "",
                             "evidence": ev}
    if not present and current:
        problems.append("the report has no ACCEPTANCE RESULTS block, so nothing "
                        "about the criteria was recorded from it")
    return {"v": VERSION, "role": role,
            "head": head if head and _SHA_FULL_RE.match(head) else None,
            "reported": present, "results": list(kept.values())[:MAX_RESULTS],
            "problems": [p[:200] for p in problems[:MAX_PROBLEMS]],
            "missing": max(0, len(current) - len(kept))}


def criteria_prompt(card_text: str, role: str) -> str:
    """The prompt's acceptance block (task 190): every criterion on the
    card with the revision the run reports it by, and the exact lines bench
    reads back. Revisions, not positions: a reorder cannot move a result
    onto another criterion, and a reworded requirement does not match."""
    items = taskfiles.criteria(taskfiles.card_parts(card_text)["brief"])
    if not items:
        return ("## Acceptance results\n\nThis card's brief has no checklist "
                "criteria, so there is nothing to report per criterion.")
    words = {"implementer": ("met", "not met", "not verified", "needs decision"),
             "reviewer": ("accepted", "not met", "not verified", "needs decision")}[role]
    rows = "\n".join(f"- `{criterion_rev(i['text'])}` — {label(i['text'], 160)}"
                     for i in items)
    if role == "implementer":
        meaning = (
            "- `met` — you implemented it and checked it; say how, and cite bench "
            "evidence where a check covers it. It reads *Met — awaiting review*: "
            "only the independent review can accept it.\n"
            "- `not met` — it is not done, or you found it cannot be; say what is "
            "missing.\n"
            "- `not verified` — done, but you could not check it; say what would.\n"
            "- `needs decision` — only a person can settle it (a product judgment "
            "or a permission); the reason is the exact question. Not for routine "
            "verification.")
    else:
        meaning = (
            "- `accepted` — at the head you reviewed the criterion is met, on "
            "substantive evidence (a file and line, a test you read, a result bench "
            "recorded). Say what it rests on.\n"
            "- `not met` — you can show it is not met; say where.\n"
            "- `not verified` — you could not establish it either way; say what "
            "evidence is missing.\n"
            "- `needs decision` — only a person can settle it; the reason is the "
            "exact question. Not for routine verification.\n"
            "Your acceptance is a reviewer's, not a person's sign-off, and it never "
            "approves a merge.")
    return (
        "## Acceptance results\n\n"
        "Report a result for every criterion below in your final reply, in a "
        "block of its own that starts with the line `ACCEPTANCE RESULTS` and has "
        "one line per criterion, then a blank line:\n\n"
        "```\nACCEPTANCE RESULTS\n- <revision>: <result> — <one-sentence reason> "
        "[evidence: <evidence id>, …]\n```\n\n"
        f"`<result>` is one of {', '.join(f'`{w}`' for w in words)}:\n{meaning}\n\n"
        "The `[evidence: …]` part is optional and names only evidence ids "
        "bench-verify printed for this card. Bench reads this block when your "
        "report is filed and records it against each criterion, the commit and "
        "this run; a criterion you leave out stays without a result, and a line "
        "it cannot read is shown as not understood. Never edit the brief to "
        "change a criterion — reporting is not rewriting the requirement.\n\n"
        f"The criteria, by revision:\n{rows}")


def provenance_line(meta: dict) -> str:
    """The marker a filed report carries under its heading — the one
    writer of the shape `read_provenance` reads (`agents` files it)."""
    kept = {k: v for k, v in meta.items() if v not in (None, "", [])}
    return taskfiles.record_line("review", {**kept, "v": PROVENANCE_VERSION})


def split_provenance(body: str) -> tuple[dict | None, str]:
    """A report's body without the provenance marker bench wrote above it,
    and the marker read. The report itself — the stored text a reader
    sees, whose digest `reportSha` is — is everything after it."""
    lines = body.split("\n")
    at = 0
    while at < len(lines) and not lines[at].strip():
        at += 1
    if at < len(lines):
        match = PROVENANCE_RE.match(lines[at])
        if match:
            return read_provenance(match.group(1)), "\n".join(lines[at + 1:]).strip("\n")
    return None, body


def report_sha(body: str) -> str:
    """A filed report's digest: the stored text exactly as a reader gets
    it, so the card, the Brain and the filing record name one value."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def rounds(text: str) -> list[dict]:
    """The card's review rounds, read off the reports agents.py appended:
    each `## PR review …` section is one round, with its reviewer, the
    revision it was pinned to, its verdict and its whole report. A
    round's identity is its heading line — stable across moves, syncs and
    restarts, because the heading is never rewritten."""
    history = taskfiles.card_parts(text)["history"]
    heads = list(REPORT_RE.finditer(history))
    out = []
    for index, head in enumerate(heads):
        match = ROUND_RE.match(head.group(0))
        if not match:
            continue
        stop = heads[index + 1].start() if index + 1 < len(heads) else len(history)
        prov, body = split_provenance(_report_body(history, head.end(), stop))
        verdict = VERDICT_RE.search(body)
        out.append({
            "id": "rv-" + hashlib.sha256(head.group(0).strip().encode("utf-8")).hexdigest()[:10],
            "heading": head.group(1).strip(),
            "commit": match.group(2),
            "label": match.group(3) or "current-at-the-time",
            "at": match.group(4), "reviewer": match.group(5) or "",
            "verdict": verdict.group(1) if verdict else None,
            "report": body,
            "reportSha": report_sha(body),
            "provenance": prov,
        })
    return out


_SECTION_RE = re.compile(
    r"^##\s+(?:Discussion|Phase log|Acceptance record|Review findings"
    r"|Orchestration record)[ \t]*$",
    re.MULTILINE)


def _report_body(history: str, start: int, stop: int) -> str:
    """One filed report's text: from its heading to the next dated report
    — or to the next board-written section (the discussion, the records),
    which is the board's, not the report's. Without that stop, a comment
    saved after the newest review would read as part of that review."""
    body = history[start:stop]
    section = _SECTION_RE.search(body)
    if section:
        body = body[:section.start()]
    body = body.strip("\n")
    while body.rstrip().endswith("---"):
        body = body.rstrip()[:-3].rstrip()
    return body


def relevance_id(heading_line: str) -> str:
    """A relevance review's stable identity: a hash of its heading line,
    which agents.py writes once and nothing rewrites — so the id survives
    moves, syncs and restarts, and two boards compute the same one."""
    return "rl-" + hashlib.sha256(heading_line.strip().encode("utf-8")).hexdigest()[:10]


def relevance_reviews(text: str) -> list[dict]:
    """The card's relevance reviews, oldest first (task 84).

    A relevance review judges whether the *brief* is still needed and
    current; it is not a review of code at a revision, so it carries no
    commit and nothing here reads it as approval, an accepted criterion or
    a merge requirement. Where a revision of the brief was not recorded —
    every review filed so far — it says so (`briefRevision: None`) rather
    than guessing one. A heading with no time or no reviewer is `legacy`
    and is shown as written."""
    history = taskfiles.card_parts(text)["history"]
    heads = list(REPORT_RE.finditer(history))
    out = []
    for index, head in enumerate(heads):
        line = head.group(0).strip()
        match = RELEVANCE_RE.match(line)
        if not match:
            continue
        stop = heads[index + 1].start() if index + 1 < len(heads) else len(history)
        prov, body = split_provenance(_report_body(history, head.end(), stop))
        said = RELEVANCE_VERDICT_RE.search(body)
        raw = said.group(1).strip() if said else None
        word = next((w for w in RELEVANCE_WORDS
                     if raw and raw.lower().startswith(w.lower())), None)
        date, clock, who = match.group(1), match.group(2), (match.group(3) or "").strip()
        out.append({
            "id": relevance_id(line), "type": "relevance",
            "heading": line.lstrip("#").strip(),
            "at": f"{date} {clock}" if clock else date,
            "reviewer": who, "verdict": word, "verdictLine": raw,
            # the brief revision the run was *launched* with (task 168),
            # recorded by bench when it filed the report; None — unknown —
            # for a review filed without one, never the brief as it is now
            "briefRevision": (prov or {}).get("briefRevision"),
            "legacy": not clock or not who,
            "report": body,
            "reportSha": report_sha(body),
            "provenance": prov,
        })
    return out


def find_relevance(text: str, review_id: str) -> dict | None:
    return next((r for r in relevance_reviews(text) if r["id"] == review_id), None)


def reports(text: str) -> list[dict]:
    """Every dated report in the card's history — work reports, PR updates,
    reviews, relevance reviews — in file order, for the Activity section.
    A review carries the identity Reviews files it under (`rv-…` for a PR
    review round, `rl-…` for a relevance review), so Activity can point at
    the one authoritative copy rather than drawing a second."""
    history = taskfiles.card_parts(text)["history"]
    heads = list(REPORT_RE.finditer(history))
    out = []
    for index, head in enumerate(heads):
        stop = heads[index + 1].start() if index + 1 < len(heads) else len(history)
        _prov, body = split_provenance(_report_body(history, head.end(), stop))
        kind = head.group(1).strip()
        line = head.group(0)
        item = {"kind": kind, "at": head.group(2), "by": head.group(3) or "",
                "text": body,
                # the report's own first paragraph, verbatim and clipped at
                # a sentence (task 149): what the Overview quotes as the
                # last recorded result, labelled as the run's account
                "headline": taskfiles.lead_paragraph(body)}
        if ROUND_RE.match(line):
            item.update(review="rv-" + hashlib.sha256(line.strip().encode("utf-8"))
                        .hexdigest()[:10], reviewType="pr")
        elif RELEVANCE_RE.match(line.strip()):
            item.update(review=relevance_id(line), reviewType="relevance")
        out.append(item)
    return out


def findings(text: str, filename: str, current: dict) -> list[dict]:
    """Every finding with its events in order and its state computed from
    the newest. Open first, then fix reported, then resolved."""
    by_id: dict = {}
    for event in _ordered(taskfiles.finding_records(text)):
        fid = event.get("finding")
        if not fid:
            continue
        if event.get("event") == "opened":
            by_id.setdefault(fid, {"id": fid, "events": []})
            by_id[fid].update(text=event.get("text") or "", round=event.get("round"),
                              criterion=event.get("criterion"),
                              opened=event.get("at"), by=event.get("actor"))
        if fid in by_id:
            by_id[fid]["events"].append(event)
    out = []
    for finding in by_id.values():
        last = finding["events"][-1]
        state = {"opened": "open", "reopened": "open",
                 "fix-reported": "fix-reported", "resolved": "resolved"}.get(
                     last.get("event"), "open")
        finding["state"] = state
        finding["word"] = FINDING_STATES[state]
        finding["basis"] = last.get("id")
        if finding.get("criterion") and finding["criterion"] not in current:
            finding["criterionRemoved"] = True
        for event in finding["events"]:
            if event.get("evidence"):
                event["evidenceNow"] = _evidence_now(str(event["evidence"]), filename)
        out.append(finding)
    order = {"open": 0, "fix-reported": 1, "resolved": 2}
    out.sort(key=lambda f: (order[f["state"]], _epoch(f.get("opened"))))
    return out


def read_text(text: str, filename: str, head: str | None = None) -> dict:
    """The whole reading of one card's text. Pure: writes nothing, assigns
    nothing. Every current criterion is read — tracked or not — from the
    person's decisions about it and the results runs reported in their
    filed reports (task 190); `head` is the candidate commit, when the
    caller knows it, which a run's result must be about to stand."""
    parts = taskfiles.card_parts(text)
    items = taskfiles.criteria(parts["brief"])
    ticks = _legacy_ticks(parts["brief"])
    records = taskfiles.acceptance_records(text)
    claims = progress.claims_for(filename)
    events, reporting = agent_results(text)
    by_criterion: dict = {}
    for record in _ordered(records):
        cid = record.get("criterion")
        if cid:
            by_criterion.setdefault(cid, []).append(record)
    tracked = any(item["id"] for item in items)
    current: dict = {}
    every, criteria, untracked, used = [], [], [], set()
    for order, item in enumerate(items):
        item = {**item, "order": order, "rev": criterion_rev(item["text"]),
                "label": label(item["text"]), "legacyTick": bool(ticks.get(order))}
        mine = by_criterion.get(item["id"], []) if item["id"] else []
        theirs = _for_item(item, events)
        used.update(e["id"] for e in theirs)
        if not mine and not item["id"] and item["legacyTick"]:
            # an untracked tick by hand is the same unverified claim a
            # tracked one is, said the same way
            mine = [{"state": "reported", "source": "legacy-tick", "at": ""}]
        found = _effective(item, mine, claims.get(item["id"]) if item["id"] else None,
                           filename, theirs, head)
        history = [r for r in mine if r.get("id")] + theirs
        row = {**item, **found, "key": item["id"] or f"@{item['rev']}",
               "tracked": bool(item["id"]), "word": STATES[found["state"]],
               # what a person's next decision is held to: the newest
               # record in the card's acceptance record, whatever a run
               # reported since (a report never makes a decision stale)
               "basis": next((r["id"] for r in reversed(mine) if r.get("id")), None),
               "history": list(reversed(_ordered(history)))}
        every.append(row)
        if item["id"]:
            current[item["id"]] = item
            criteria.append(row)
        else:
            untracked.append(row)
    removed = [{"id": cid, "text": (recs[-1].get("text") or ""),
                "decision": recs[-1], "history": list(reversed(recs))}
               for cid, recs in by_criterion.items() if cid not in current]
    orphans = [e for e in events if e["id"] not in used]
    counts = {state: 0 for state in STATES}
    for item in every:
        counts[item["state"]] += 1
    found_list = findings(text, filename, current)
    return {
        "file": filename,
        "briefSha": taskfiles.brief_sha(parts["brief"]),
        # whether any criterion carries an id yet; ids are assigned on an
        # authorized write (a saved brief, a run's report, a decision) —
        # never by reading (task 190)
        "tracked": tracked,
        # every current criterion, in the brief's order, tracked or not
        "items": every,
        "criteria": criteria, "untracked": untracked, "removed": removed,
        # results reported about a requirement that is no longer on the
        # card: history, never current
        "orphanResults": list(reversed(orphans)),
        "reporting": list(reversed(reporting)),
        "legacyTicks": sum(1 for v in ticks.values() if v),
        "counts": {**counts, "total": len(every)},
        "outcome": outcome(every, reporting, head),
        "head": (head or "")[:12] or None,
        "rounds": rounds(text),
        # relevance reviews are their own kind (task 84): they judge the
        # brief, not code, and feed no count, verdict or gate above
        "relevance": relevance_reviews(text),
        "findings": found_list,
        "findingCounts": {state: sum(1 for f in found_list if f["state"] == state)
                          for state in FINDING_STATES},
        "states": STATES, "findingStates": FINDING_STATES,
    }


def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def outcome(items: list[dict], reporting: list[dict], head: str | None = None) -> dict:
    """The aggregate a reader sees first (task 190): one headline, the
    counts of the criteria displayed, and who must do what next. Derived
    from the very rows the Acceptance section lists, so the two cannot
    disagree — and the one reading the Overview draws too. An all-accepted
    checklist is a reviewer's or a person's conclusion about the criteria,
    never a statement that the card may merge."""
    counts = {state: 0 for state in STATES}
    for item in items:
        counts[item["state"]] += 1
    total = len(items)
    parts = [{"state": s, "word": STATES[s], "count": counts[s]}
             for s in STATE_ORDER if counts[s]]
    latest = reporting[-1] if reporting else None
    problems = list((latest or {}).get("problems") or [])

    def said(state: str, headline: str, owner, next_: str, tone: str) -> dict:
        return {"state": state, "headline": headline, "owner": owner,
                "next": next_, "tone": tone, "counts": counts, "parts": parts,
                "total": total, "problems": problems,
                "latest": latest}

    if not total:
        return said("none", "Acceptance: no criteria", None,
                    "The brief has no checklist criteria (lines like `- [ ] …`), so "
                    "there is nothing to report against. Add them to the brief if "
                    "this card should have them.", "neutral")
    if counts["needs-work"]:
        return said("changes-needed", "Acceptance: changes needed", "agent",
                    f"The implementation agent fixes {_n(counts['needs-work'], 'criterion', 'criteria')} "
                    f"not met — ↻ act on PR in review, ▸ start work before it — and "
                    f"the next review checks again.", "alarm")
    if counts["decision"]:
        first = next((i for i in items if i["state"] == "decision"), {})
        return said("decision", "Acceptance: needs your decision", "you",
                    f"Answer {'the question' if counts['decision'] == 1 else str(counts['decision']) + ' questions'} "
                    f"below and record your decision on the criterion — first: "
                    f"“{label(first.get('reason') or first.get('text') or '', 140)}”.",
                    "alarm")
    if counts["recheck"]:
        return said("recheck", "Acceptance: needs recheck", "reviewer",
                    f"{_n(counts['recheck'], 'earlier result')} no longer "
                    f"{'holds' if counts['recheck'] == 1 else 'hold'} for the current "
                    f"code or wording. The next independent review (◔ review PR) "
                    f"assesses {'it' if counts['recheck'] == 1 else 'them'} again.",
                    "neutral")
    if counts["reported"]:
        return said("awaiting-review", "Acceptance: awaiting review", "reviewer",
                    f"The implementer reported {_n(counts['reported'], 'criterion', 'criteria')} met. "
                    f"The independent review (◔ review PR) accepts or rejects each "
                    f"one — only its result, or a person's, reads Accepted.", "neutral")
    if counts["not-verified"]:
        return said("incomplete", "Acceptance: incomplete", "reviewer",
                    f"{_n(counts['not-verified'], 'criterion', 'criteria')} could not be "
                    f"verified. The next review needs evidence that covers "
                    f"{'it' if counts['not-verified'] == 1 else 'them'}; a person can "
                    f"also record a decision.", "neutral")
    if counts["pending"]:
        if counts["pending"] == total:
            return said("no-results", "Acceptance: no results yet", "agent",
                        "Results are recorded per criterion when the implementation "
                        "run files its report, then the independent review confirms "
                        "them. Nothing is inferred from the stage.", "neutral")
        return said("incomplete", "Acceptance: incomplete", "agent",
                    f"{_n(counts['pending'], 'criterion', 'criteria')} "
                    f"{'has' if counts['pending'] == 1 else 'have'} no result yet — "
                    f"the next run reports on {'it' if counts['pending'] == 1 else 'them'}.",
                    "neutral")
    by_reviewer = sum(1 for i in items if i.get("by") == "reviewer")
    return said("accepted", "Acceptance: all criteria accepted", None,
                ("Accepted by the independent review" if by_reviewer == total else
                 "Accepted on record") +
                " — a conclusion about the criteria, not a merge approval. The "
                "merge requirements decide what happens next.", "calm")


def heads_by_card(stems=None) -> dict:
    """Every card's candidate commit, in one read of the application's
    branch refs — `phase/<stem>` before `task/<stem>`, the order a merge
    reads — so a whole board's acceptance costs one git call, not one per
    card. A read; nothing moves."""
    try:
        found = config.git_run(config.REPO, "for-each-ref", "--format=%(refname) %(objectname)",
                               "refs/heads/" + config.TASK_BRANCH_PREFIX.rstrip("/"),
                               "refs/heads/" + config.PHASE_BRANCH_PREFIX.rstrip("/"),
                               timeout=10)
    except (subprocess.SubprocessError, OSError):
        return {}
    if found.returncode != 0:
        return {}
    task, phase = {}, {}
    for line in found.stdout.splitlines():
        ref, _, sha = line.partition(" ")
        name = ref[len("refs/heads/"):]
        if name.startswith(config.PHASE_BRANCH_PREFIX):
            phase[name[len(config.PHASE_BRANCH_PREFIX):]] = sha
        elif name.startswith(config.TASK_BRANCH_PREFIX):
            task[name[len(config.TASK_BRANCH_PREFIX):]] = sha
    return {**task, **phase}


def _card_path(filename: str, stage: str) -> Path:
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    if stage not in config.STAGE_DIRS:
        raise ValueError("unknown stage")
    path = config.TASKS / stage / filename
    if not path.is_file():
        raise ValueError(f"{filename} is not in {stage}/ — refresh the board")
    return path


def for_card(filename: str, stage: str) -> dict:
    """What the card's Acceptance, Reviews and Activity sections show — a
    GET: it reads the card, bench's evidence, the runs' progress and the
    card's branch tip (the candidate a run's result must be about), and
    writes nothing."""
    text = _card_path(filename, stage).read_text(encoding="utf-8")
    out = read_text(text, filename, head=branch_head(filename))
    out.update(stage=stage, reports=reports(text), actor=actor(),
               persistence={"commits": bool(config.COMMIT_MOVES),
                            "syncs": bool(getattr(config, "SYNC", False))})
    return out


def _worth_reading(text: str) -> bool:
    return ("ac:" in text or "bench:finding" in text
            or ('"acceptance"' in text and "bench:review" in text))


def summary(text: str, filename: str, head: str | None = None) -> dict | None:
    """The card face's few numbers and the outcome, or None when there is
    nothing to say: a card with no tracked criteria, no reported results
    and no findings carries no count."""
    if not _worth_reading(text):
        return None
    found = read_text(text, filename, head=head)
    if not found["tracked"] and not found["findings"] and not found["reporting"]:
        return None
    counts = found["counts"]
    result = found["outcome"]
    return {"tracked": found["tracked"], "accepted": counts["accepted"],
            "total": counts["total"], "pending": counts["pending"],
            "reported": counts["reported"], "needsWork": counts["needs-work"],
            "recheck": counts["recheck"], "notVerified": counts["not-verified"],
            "decision": counts["decision"],
            "outcome": {k: result[k] for k in ("state", "headline", "owner", "next", "tone")},
            "openFindings": found["findingCounts"]["open"],
            "fixReported": found["findingCounts"]["fix-reported"]}


def board(collected: dict) -> dict:
    """Per card, the face's summary. Empty on a board that tracks nothing —
    the law every other chip here follows. The candidate commits are read
    once, and only when some card has something to say."""
    out, heads = {}, None
    for group in collected.get("stages") or []:
        for task in group.get("tasks") or []:
            body = task.get("body") or ""
            if not _worth_reading(body):
                continue
            if heads is None:
                heads = heads_by_card()
            try:
                found = summary(body, task["file"], heads.get(task["file"][:-3]))
            except (OSError, ValueError):
                found = None
            if found:
                out[task["file"]] = found
    return out


# ── writing ────────────────────────────────────────────────────────────


def actor() -> dict:
    """Who a decision made from this board is attributed to: the work
    state's git identity, else the operating-system user — said which, so
    an attribution is never invented and never left blank."""
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


def _clean(name: str, value, cap: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"'{name}' must be text")
    value = value.replace("\r\n", "\n").replace("\t", " ").strip()
    if _CONTROL.search(value):
        raise ValueError(f"'{name}' holds control or bidirectional characters")
    if len(value) > cap:
        raise ValueError(f"'{name}' is {len(value)} characters; the limit is {cap}")
    if required and len(value) < 3:
        raise ValueError(f"a {name} is required — say what was checked and how")
    return value


def _record_id(prefix: str, filename: str, request) -> str:
    """A record's identity, derived from the page's request key when it
    sent one — so a double click, a resend or a refresh mid-request finds
    the record it already made rather than writing a second."""
    if request:
        if not isinstance(request, str) or not _REQUEST_RE.match(request):
            raise ValueError("a request key is 8–80 of letters, digits, . _ : -")
        return f"{prefix}-" + hashlib.sha256(
            f"{filename}|{request}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{secrets.token_hex(5)}"


def branch_head(filename: str) -> str | None:
    """The tip of the card's branch in the application, if it has one — the
    code revision a manual decision was made against. Read, never moved.

    `phase/<stem>` before `task/<stem>`: the order `config.branch_for` and
    `gates.head_for` pick the commit a merge would take in, so a sign-off
    is recorded against the very commit the gate reads it about. Asked the
    other way round, a card holding both branches recorded every decision
    against the task tip and could never meet its sign-off."""
    stem = filename[:-3]
    for branch in (config.PHASE_BRANCH_PREFIX + stem,
                   config.TASK_BRANCH_PREFIX + stem):
        try:
            found = config.git_run(config.REPO, "rev-parse", "--verify", "--quiet",
                                   f"refs/heads/{branch}", timeout=10)
        except (subprocess.SubprocessError, OSError):
            return None
        if found.returncode == 0 and _SHA_RE.match(found.stdout.strip()):
            return found.stdout.strip()[:12]
    return None


def track(filename: str, stage: str, *, brief_sha: str, request=None) -> dict:
    """Start tracking a card's acceptance: give every criterion that has no
    identity one, on its own line, and record it `pending` — or, where it
    carried a hand-written `[x]`, `reported` as a legacy claim, its
    original tick and text kept exactly. The explicit operation the v1
    contract left to card 81; viewing a card never does this.

    Bound to the brief the person was shown: a brief that has changed since
    is refused, nothing written. Ids are fresh and never reused — not even
    one a removed criterion once held, so its history cannot be adopted by
    a stranger."""
    _card_path(filename, stage)
    who = _who()
    at = taskfiles.now_iso()
    if request is not None:
        _record_id("t", filename, request)          # validated, nothing else

    def change(text: str):
        parts = taskfiles.card_parts(text)
        if taskfiles.brief_sha(parts["brief"]) != brief_sha:
            raise taskfiles.StaleCard(
                f"the brief of {filename} has changed since you read it — "
                f"nothing was tracked; refresh and track again")
        updated, found = assign_ids(text, who, at)
        if not found["items"]:
            raise ValueError(f"{filename} has no checklist criteria (`- [ ] …`) "
                             f"to track")
        if not found["tracked"]:
            raise ValueError(f"every criterion on {filename} is already tracked")
        return updated, {"tracked": found["tracked"], "legacyTicks": found["legacyTicks"]}

    return taskfiles.edit_card(filename, stage, change,
                               "acceptance tracked")


def adopt(filename: str, stage: str, *, root: Path | None = None) -> dict:
    """Give every criterion without an identity one — the write task 190
    makes part of an authorized write (a run's report being filed, a saved
    brief, a person's decision) so nobody has to Track acceptance. Its
    wording, its tick and every id it already had are kept exactly; a card
    with nothing untracked is not written at all, so it commits nothing.
    Idempotent: the second call finds every criterion tracked."""
    if root is not None and Path(root).resolve() != Path(config.TASKS).resolve():
        return {"tracked": [], "written": False, "skipped": "another board"}
    path = _card_path(filename, stage)
    text = path.read_text(encoding="utf-8")
    items = taskfiles.criteria(taskfiles.card_parts(text)["brief"])
    if not any(not item["id"] for item in items):
        return {"tracked": [], "written": False}
    who = actor()
    who = who if who["name"] else {"name": "board", "source": "board"}
    at = taskfiles.now_iso()

    def change(card: str):
        updated, found = assign_ids(card, who, at)
        return updated, {"tracked": found["tracked"], "legacyTicks": found["legacyTicks"]}

    return taskfiles.edit_card(filename, stage, change, "acceptance criteria identified")


def assign_ids(text: str, who: dict, at: str) -> tuple[str, dict]:
    """The one writer of criterion identities: every checklist line without
    `<!-- ac:<id> -->` gains a fresh one at its end, and a record — pending,
    or a hand-written tick kept as the unverified claim it is. Fresh ids
    only: never one the card's brief or records already hold, so a removed
    criterion's history cannot be adopted by a stranger. Pure on text."""
    parts = taskfiles.card_parts(text)
    taken = {c["id"] for c in taskfiles.criteria(parts["brief"]) if c["id"]}
    taken |= {r.get("criterion") for r in taskfiles.acceptance_records(text)}
    lines = parts["brief"].splitlines(keepends=True)
    fenced, items = False, []
    for index, line in enumerate(lines):
        if taskfiles.is_fence(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if taskfiles.CRITERION_RE.match(line):
            items.append([index, line])
        elif items and line.strip() and line[:1].isspace() \
                and items[-1][0] + len(items[-1]) - 2 == index - 1:
            # [first index, first line, continuation lines…]: a line
            # continues the criterion directly above it, as `criteria`
            # reads it
            items[-1].append(line)
    added, ticks = [], 0
    for item in items:
        index, raw = item[0], "".join(item[1:])
        if taskfiles.CRITERION_ID_RE.search(raw):
            continue
        cid = "a" + secrets.token_hex(3)
        while cid in taken:
            cid = "a" + secrets.token_hex(3)
        taken.add(cid)
        line = lines[index]
        body = line.rstrip("\r\n")
        lines[index] = f"{body} <!-- ac:{cid} -->" + line[len(body):]
        text_of = " ".join(taskfiles.CRITERION_ID_RE.sub(
            "", taskfiles.CRITERION_RE.match(raw.split("\n")[0]).group(1)
            + " " + " ".join(l.strip() for l in item[2:])).split())
        ticked = bool(re.match(r"^\s*[-*+]\s+\[[xX]\]", line))
        ticks += ticked
        added.append({"v": VERSION, "id": f"r-{secrets.token_hex(5)}",
                      "criterion": cid, "rev": criterion_rev(text_of),
                      "state": "reported" if ticked else "pending",
                      "source": "legacy-tick" if ticked else "tracked",
                      "actor": who["name"], "actorSource": who["source"],
                      "at": at, "text": text_of[:600]})
    if not added:
        return text, {"items": items, "tracked": [], "legacyTicks": 0}
    updated = parts["head"] + "".join(lines) + parts["history"]
    for record in added:
        updated = taskfiles.append_record(
            updated, taskfiles.ACCEPTANCE_HEADING, taskfiles.ACCEPTANCE_NOTE,
            taskfiles.record_line("acceptance", record))
    return updated, {"items": items, "tracked": [r["criterion"] for r in added],
                     "legacyTicks": ticks}


def decide(filename: str, stage: str, *, criterion: str, state: str,
           rev: str, basis, note=None, ref=None, evidence=None,
           request=None) -> dict:
    """Record one person's decision about one criterion.

    `accepted` needs one of two things and nothing else: a note saying what
    was checked (a manual or visual confirmation, optionally with a
    reference), or one of bench's own evidence records for this card that
    reads `passed` *now* — mapped to this criterion by the person making
    the decision, never inferred from a check's name or a test count.
    `needs-work` and `recheck` need a note; `pending` clears a decision
    back to nothing decided, and says why.

    Refused, with nothing written, when what the person was shown is no
    longer true: the criterion's wording (`rev`) or the newest decision
    about it (`basis`) has moved on — so a stale page, a replayed request
    or a slower concurrent user cannot overwrite a newer decision."""
    _card_path(filename, stage)
    if state not in DECISIONS:
        raise ValueError(f"'{state}' is not a decision — one of "
                         f"{', '.join(DECISIONS)}")
    who = _who()
    note = _clean("note", note, MAX_NOTE,
                  required=(state != "pending" and not evidence) or state != "accepted")
    ref = _clean("reference", ref, MAX_REF)
    if "\n" in ref:
        raise ValueError("a reference is one line")
    ev = None
    if evidence:
        if state != "accepted":
            raise ValueError("evidence supports an acceptance; record the other "
                             "decisions with a note")
        ev = _evidence_now(str(evidence), filename)
        if not ev["found"]:
            raise ValueError(f"evidence {evidence} is not a record bench made for "
                             f"{filename}")
        if ev["outcome"] != "passed":
            raise ValueError(f"evidence {evidence} reads {ev['outcome']} — only "
                             f"a current pass can support an acceptance")
    record_id = _record_id("r", filename, request)
    at = taskfiles.now_iso()

    def change(text: str):
        if any(r.get("id") == record_id for r in taskfiles.acceptance_records(text)):
            return text, {"duplicate": True, "id": record_id}
        cid, fresh = criterion, False
        if str(cid).startswith("@"):
            # a criterion that has no identity yet, named by the revision
            # the person read (task 190): the decision is the authorized
            # write that gives the card's criteria their ids, in this same
            # edit, then records against the one they read
            text, _assigned = assign_ids(text, who, at)
            want = str(cid)[1:]
            match = [c for c in taskfiles.criteria(taskfiles.card_parts(text)["brief"])
                     if c["id"] and criterion_rev(c["text"]) == want]
            if not match:
                raise taskfiles.StaleCard(
                    "that criterion was reworded or removed since you read it — "
                    "nothing was recorded; read it again and decide")
            cid, fresh = match[0]["id"], True
        parts = taskfiles.card_parts(text)
        items = {c["id"]: c for c in taskfiles.criteria(parts["brief"]) if c["id"]}
        if cid not in items:
            raise ValueError(f"{filename} has no tracked criterion '{cid}' — "
                             f"a decision is about a criterion on this card")
        current = criterion_rev(items[cid]["text"])
        if rev != current:
            raise taskfiles.StaleCard(
                f"the criterion '{cid}' was reworded since you read it — "
                f"nothing was recorded; read it again and decide")
        mine = _ordered([r for r in taskfiles.acceptance_records(text)
                         if r.get("criterion") == cid])
        newest = mine[-1].get("id") if mine else None
        if fresh and not basis and all(r.get("source") in ("tracked", "legacy-tick")
                                       for r in mine):
            basis_ok = True
        else:
            basis_ok = (basis or None) == newest
        criterion_id = cid
        if not basis_ok:
            raise taskfiles.StaleCard(
                f"a newer decision about '{criterion}' was recorded since you "
                f"read it — nothing was recorded; read it and decide again")
        record = {"v": VERSION, "id": record_id, "criterion": criterion_id,
                  "rev": current, "state": state,
                  "source": "evidence" if ev else "manual",
                  "actor": who["name"], "actorSource": who["source"], "at": at,
                  "text": items[criterion_id]["text"][:600]}
        if note:
            record["note"] = note
        if ref:
            record["ref"] = ref
        if ev:
            record.update(evidence=ev["id"], check=ev.get("check"),
                          scope=ev.get("scope"), commit=ev.get("commit"))
        else:
            head = branch_head(filename)
            if head:
                record["commit"] = head
        if newest:
            record["supersedes"] = newest
        updated = taskfiles.append_record(
            text, taskfiles.ACCEPTANCE_HEADING, taskfiles.ACCEPTANCE_NOTE,
            taskfiles.record_line("acceptance", record))
        return updated, {"duplicate": False, "id": record_id, "record": record,
                         "criterion": criterion_id}

    return taskfiles.edit_card(filename, stage, change,
                               f"acceptance {criterion} → {state}")


def finding(filename: str, stage: str, *, event: str, finding_id=None,
            text=None, round_id=None, criterion=None, note=None,
            evidence=None, basis=None, request=None) -> dict:
    """Record one event in a review finding's life.

    `opened` states a finding, optionally against a review round and a
    criterion; `fix-reported` says a fix exists and keeps the finding open
    until somebody rechecks it; `resolved` closes it and records on what
    authority (a note, and optionally a passing evidence record) and at
    which revision; `reopened` puts it back. A later agent update never
    resolves anything — only this operation, attributed, does."""
    _card_path(filename, stage)
    if event not in FINDING_EVENTS:
        raise ValueError(f"'{event}' is not a finding event — one of "
                         f"{', '.join(FINDING_EVENTS)}")
    who = _who()
    body = _clean("finding", text, MAX_FINDING, required=event == "opened")
    note = _clean("note", note, MAX_NOTE, required=event != "opened")
    ev = None
    if evidence:
        if event not in ("resolved", "fix-reported"):
            raise ValueError("evidence supports a fix or a resolution")
        ev = _evidence_now(str(evidence), filename)
        if not ev["found"]:
            raise ValueError(f"evidence {evidence} is not a record bench made for "
                             f"{filename}")
        if ev["outcome"] != "passed":
            raise ValueError(f"evidence {evidence} reads {ev['outcome']} — only "
                             f"a current pass can support it")
    event_id = _record_id("e", filename, request)
    at = taskfiles.now_iso()

    def change(card: str):
        known = taskfiles.finding_records(card)
        if any(r.get("id") == event_id for r in known):
            return card, {"duplicate": True, "id": event_id}
        parts = taskfiles.card_parts(card)
        items = {c["id"] for c in taskfiles.criteria(parts["brief"]) if c["id"]}
        record = {"v": VERSION, "id": event_id, "event": event,
                  "actor": who["name"], "actorSource": who["source"], "at": at}
        if event == "opened":
            if round_id and round_id not in {r["id"] for r in rounds(card)}:
                raise ValueError(f"{filename} has no review round '{round_id}'")
            if criterion and criterion not in items:
                raise ValueError(f"{filename} has no tracked criterion '{criterion}'")
            record.update(finding="f-" + event_id[2:], text=body)
            if round_id:
                record["round"] = round_id
            if criterion:
                record["criterion"] = criterion
        else:
            mine = _ordered([r for r in known if r.get("finding") == finding_id])
            if not finding_id or not mine or mine[0].get("event") != "opened":
                raise ValueError(f"{filename} has no finding '{finding_id}'")
            last = mine[-1]
            if (basis or None) != last.get("id"):
                raise taskfiles.StaleCard(
                    f"finding {finding_id} changed since you read it — nothing "
                    f"was recorded; read it again")
            state = {"opened": "open", "reopened": "open"}.get(
                last.get("event"), last.get("event"))
            allowed = {"fix-reported": {"open"}, "resolved": {"open", "fix-reported"},
                       "reopened": {"fix-reported", "resolved"}}[event]
            if state not in allowed:
                raise ValueError(f"finding {finding_id} is {state}; it cannot be "
                                 f"marked {event}")
            record.update(finding=finding_id, note=note, supersedes=last.get("id"))
            if ev:
                record.update(evidence=ev["id"], check=ev.get("check"),
                              commit=ev.get("commit"))
            else:
                head = branch_head(filename)
                if head:
                    record["commit"] = head
        updated = taskfiles.append_record(
            card, taskfiles.FINDINGS_HEADING, taskfiles.FINDINGS_NOTE,
            taskfiles.record_line("finding", record))
        return updated, {"duplicate": False, "id": event_id, "record": record}

    return taskfiles.edit_card(filename, stage, change, f"review finding {event}")
