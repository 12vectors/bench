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
import re
import secrets
import subprocess
import time
from pathlib import Path

import config
import progress
import taskfiles
import verify

VERSION = 1

# What a criterion's state *licenses a reader to believe*, in words. The
# word carries the meaning; colour never stands alone.
STATES = {
    "pending": "pending",
    "reported": "reported — awaiting confirmation",
    "accepted": "accepted",
    "needs-work": "needs work",
    "recheck": "needs recheck",
}
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
        if taskfiles._is_fence(line):
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
    record = verify.read(evidence_id) if verify.is_evidence_id(evidence_id) else None
    if record is None or record.get("task") != filename:
        return {"id": evidence_id, "found": False, "outcome": "missing"}
    now = verify.outcome_now(record)
    source = record.get("source") or {}
    return {"id": evidence_id, "found": True, "check": record.get("check"),
            "scope": record.get("scope"), "outcome": now.get("outcome"),
            "reason": now.get("reason"), "why": now.get("why"),
            "commit": (source.get("commit") or "")[:12] or None,
            "ended": record.get("ended")}


def _effective(item: dict, records: list[dict], claim: dict | None,
               filename: str) -> dict:
    """One criterion's state, computed — never stored. The newest decision
    stands, unless what it was about has moved: the requirement reworded
    (its revision differs), the record marked for recheck by an applied
    refinement, or the evidence it rested on no longer reading `passed`.
    Each of those is `recheck` with the reason, and the decision itself is
    kept and shown — the prior result is history, not deleted."""
    decision = records[-1] if records else None
    out = {"state": "pending", "reason": None, "decision": decision,
           "evidence": None, "claim": claim}
    claim_at = (claim or {}).get("at") or 0
    if decision is None:
        if claim:
            out.update(state="reported", reason="an agent reported it implemented")
        return out
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
            out["state"] = "accepted"
    elif state == "needs-work":
        if claim and claim_at > decided_at:
            out.update(state="reported",
                       reason="a fix was reported after it was marked as needing work")
        else:
            out["state"] = "needs-work"
    elif state == "recheck":
        out.update(state="recheck", reason=decision.get("note") or "recheck requested")
    elif state == "reported":
        out.update(state="reported",
                   reason=("ticked by hand before tracking — a legacy claim, "
                           "not an acceptance") if decision.get("source") == "legacy-tick"
                   else "reported")
    else:
        if claim and claim_at > decided_at:
            out.update(state="reported", reason="an agent reported it implemented")
    return out


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
        body = history[head.end():stop].strip("\n")
        while body.rstrip().endswith("---"):
            body = body.rstrip()[:-3].rstrip()
        verdict = VERDICT_RE.search(body)
        out.append({
            "id": "rv-" + hashlib.sha256(head.group(0).strip().encode("utf-8")).hexdigest()[:10],
            "heading": head.group(1).strip(),
            "commit": match.group(2),
            "label": match.group(3) or "current-at-the-time",
            "at": match.group(4), "reviewer": match.group(5) or "",
            "verdict": verdict.group(1) if verdict else None,
            "report": body,
        })
    return out


def reports(text: str) -> list[dict]:
    """Every dated report in the card's history — work reports, PR updates,
    reviews, relevance reviews — in file order, for the Activity section."""
    history = taskfiles.card_parts(text)["history"]
    heads = list(REPORT_RE.finditer(history))
    out = []
    for index, head in enumerate(heads):
        stop = heads[index + 1].start() if index + 1 < len(heads) else len(history)
        body = history[head.end():stop].strip("\n")
        while body.rstrip().endswith("---"):
            body = body.rstrip()[:-3].rstrip()
        kind = head.group(1).strip()
        out.append({"kind": kind, "at": head.group(2), "by": head.group(3) or "",
                    "text": body})
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


def read_text(text: str, filename: str) -> dict:
    """The whole reading of one card's text. Pure: writes nothing, assigns
    nothing — a card nobody has tracked reads `not-recorded`, with its
    checklist and any hand-written ticks shown as exactly that."""
    parts = taskfiles.card_parts(text)
    items = taskfiles.criteria(parts["brief"])
    ticks = _legacy_ticks(parts["brief"])
    records = taskfiles.acceptance_records(text)
    claims = progress.claims_for(filename)
    by_criterion: dict = {}
    for record in _ordered(records):
        cid = record.get("criterion")
        if cid:
            by_criterion.setdefault(cid, []).append(record)
    tracked = any(item["id"] for item in items)
    current: dict = {}
    criteria, untracked = [], []
    for order, item in enumerate(items):
        item = {**item, "order": order, "rev": criterion_rev(item["text"]),
                "label": label(item["text"]), "legacyTick": bool(ticks.get(order))}
        if not item["id"]:
            untracked.append(item)
            continue
        current[item["id"]] = item
        mine = by_criterion.get(item["id"], [])
        found = _effective(item, mine, claims.get(item["id"]), filename)
        criteria.append({**item, **found, "word": STATES[found["state"]],
                         "basis": (found["decision"] or {}).get("id"),
                         "history": list(reversed(mine))})
    removed = [{"id": cid, "text": (recs[-1].get("text") or ""),
                "decision": recs[-1], "history": list(reversed(recs))}
               for cid, recs in by_criterion.items() if cid not in current]
    counts = {state: 0 for state in STATES}
    for item in criteria:
        counts[item["state"]] += 1
    counts["pending"] += len(untracked)
    found_list = findings(text, filename, current)
    return {
        "file": filename,
        "briefSha": taskfiles.brief_sha(parts["brief"]),
        # `tracked` is the one fact the face and the Acceptance section
        # branch on: an untracked card reads "Acceptance results not
        # recorded", never a count.
        "tracked": tracked,
        "criteria": criteria, "untracked": untracked, "removed": removed,
        "legacyTicks": sum(1 for v in ticks.values() if v),
        "counts": {**counts, "total": len(criteria) + len(untracked)},
        "rounds": rounds(text),
        "findings": found_list,
        "findingCounts": {state: sum(1 for f in found_list if f["state"] == state)
                          for state in FINDING_STATES},
        "states": STATES, "findingStates": FINDING_STATES,
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


def for_card(filename: str, stage: str) -> dict:
    """What the card's Acceptance, Reviews and Activity sections show — a
    GET: it reads the card, bench's evidence and the runs' progress, and
    writes nothing."""
    text = _card_path(filename, stage).read_text(encoding="utf-8")
    out = read_text(text, filename)
    out.update(stage=stage, reports=reports(text), actor=actor(),
               persistence={"commits": bool(config.COMMIT_MOVES),
                            "syncs": bool(getattr(config, "SYNC", False))})
    return out


def summary(text: str, filename: str) -> dict | None:
    """The card face's few numbers, or None when there is nothing to say:
    a card with no tracked criteria and no findings carries no count."""
    if "ac:" not in text and "bench:finding" not in text:
        return None
    found = read_text(text, filename)
    if not found["tracked"] and not found["findings"]:
        return None
    counts = found["counts"]
    return {"tracked": found["tracked"], "accepted": counts["accepted"],
            "total": counts["total"], "pending": counts["pending"],
            "reported": counts["reported"], "needsWork": counts["needs-work"],
            "recheck": counts["recheck"],
            "openFindings": found["findingCounts"]["open"],
            "fixReported": found["findingCounts"]["fix-reported"]}


def board(collected: dict) -> dict:
    """Per card, the face's summary. Empty on a board that tracks nothing —
    the law every other chip here follows."""
    out = {}
    for group in collected.get("stages") or []:
        for task in group.get("tasks") or []:
            try:
                found = summary(task.get("body") or "", task["file"])
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


def _branch_head(filename: str) -> str | None:
    """The tip of the card's branch in the application, if it has one — the
    code revision a manual decision was made against. Read, never moved."""
    stem = filename[:-3]
    for branch in (f"task/{stem}", f"phase/{stem}"):
        try:
            found = subprocess.run(
                ["git", "-C", str(config.REPO), "rev-parse", "--verify", "--quiet",
                 f"refs/heads/{branch}"], capture_output=True, text=True, timeout=10)
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
        taken = {c["id"] for c in taskfiles.criteria(parts["brief"]) if c["id"]}
        taken |= {r.get("criterion") for r in taskfiles.acceptance_records(text)}
        lines = parts["brief"].splitlines(keepends=True)
        fenced, items = False, []
        for index, line in enumerate(lines):
            if taskfiles._is_fence(line):
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
        if not items:
            raise ValueError(f"{filename} has no checklist criteria (`- [ ] …`) "
                             f"to track")
        if not added:
            raise ValueError(f"every criterion on {filename} is already tracked")
        updated = parts["head"] + "".join(lines) + parts["history"]
        for record in added:
            updated = taskfiles.append_record(
                updated, taskfiles.ACCEPTANCE_HEADING, taskfiles.ACCEPTANCE_NOTE,
                taskfiles.record_line("acceptance", record))
        return updated, {"tracked": [r["criterion"] for r in added],
                         "legacyTicks": ticks}

    return taskfiles.edit_card(filename, stage, change,
                               "acceptance tracked")


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
        parts = taskfiles.card_parts(text)
        items = {c["id"]: c for c in taskfiles.criteria(parts["brief"]) if c["id"]}
        if criterion not in items:
            raise ValueError(f"{filename} has no tracked criterion '{criterion}' — "
                             f"a decision is about a criterion on this card")
        current = criterion_rev(items[criterion]["text"])
        if rev != current:
            raise taskfiles.StaleCard(
                f"the criterion '{criterion}' was reworded since you read it — "
                f"nothing was recorded; read it again and decide")
        mine = _ordered([r for r in taskfiles.acceptance_records(text)
                         if r.get("criterion") == criterion])
        newest = mine[-1].get("id") if mine else None
        if (basis or None) != newest:
            raise taskfiles.StaleCard(
                f"a newer decision about '{criterion}' was recorded since you "
                f"read it — nothing was recorded; read it and decide again")
        record = {"v": VERSION, "id": record_id, "criterion": criterion,
                  "rev": current, "state": state,
                  "source": "evidence" if ev else "manual",
                  "actor": who["name"], "actorSource": who["source"], "at": at,
                  "text": items[criterion]["text"][:600]}
        if note:
            record["note"] = note
        if ref:
            record["ref"] = ref
        if ev:
            record.update(evidence=ev["id"], check=ev.get("check"),
                          scope=ev.get("scope"), commit=ev.get("commit"))
        else:
            head = _branch_head(filename)
            if head:
                record["commit"] = head
        if newest:
            record["supersedes"] = newest
        updated = taskfiles.append_record(
            text, taskfiles.ACCEPTANCE_HEADING, taskfiles.ACCEPTANCE_NOTE,
            taskfiles.record_line("acceptance", record))
        return updated, {"duplicate": False, "id": record_id, "record": record}

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
                head = _branch_head(filename)
                if head:
                    record["commit"] = head
        updated = taskfiles.append_record(
            card, taskfiles.FINDINGS_HEADING, taskfiles.FINDINGS_NOTE,
            taskfiles.record_line("finding", record))
        return updated, {"duplicate": False, "id": event_id, "record": record}

    return taskfiles.edit_card(filename, stage, change, f"review finding {event}")
