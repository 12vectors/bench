"""Comment threads and the Brain Agent (task 210): asking about a selected
passage, and settling a thread against what an applied change did.

`braincomments` owns the record and where a passage is; `brainwrite`
writes it; `braindraft` owns the drafting request. This module joins them
and owns nothing of its own:

- **Post comment** (`write`) appends one event through the Brain's writer.
  It launches nothing.
- **Ask agent** (`ask`) is a separate, explicit request: the thread's
  opening (or a follow-up) is written first, then exactly one Brain Agent
  update request is started through `braindraft.start` — the existing
  authorized drafting path, its preflight, profile and budget — carrying
  the entry, the exact passage and the instruction, and the thread is
  linked to it. Every step is keyed by the person's request identity, so a
  double click, a lost reply or a restart part way finds what is already
  there: one thread, one drafting request, one link.
- **Settle** (`settle`) reads a linked request's outcome. Applied, a
  thread is resolved only when the change record is exact, was made from
  the revision the thread was asked about, and its edits touch the
  anchored lines; otherwise it stays open and says why (`unrelated`,
  `unverified`). A failed Apply's conflict, a cancel or a discard is
  recorded as the thread's outcome, and the thread stays open. Settling
  never writes the entry.

Reading (`reading`) adds each linked request's live state from this
board's private drafting records; another board that synced the thread
reads its outcome records and says the request is not on this board.
"""

from __future__ import annotations

import hashlib
import threading

import brain
import braincomments
import braindraft
import brainwrite
import state

LOCK = threading.RLock()
ATTEMPTS = 4


class Refused(ValueError):
    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def _refused(exc) -> Refused:
    return Refused(str(exc), getattr(exc, "status", 400), **getattr(exc, "extra", {}))


def derived(*parts: str) -> str:
    """A request identity derived from a person's request and a step, so
    every step of one request has its own identity and a repeat finds it."""
    return "bt." + hashlib.sha256("\x00".join(map(str, parts)).encode("utf-8")).hexdigest()[:32]


def _events(entry_id: str) -> list[dict]:
    return braincomments.parse(braincomments.current_bytes(entry_id))["events"]


def _append(entry_id: str, request: str, payload: dict) -> dict:
    """The board's own event, appended against the record as it is now:
    re-read and tried again when another writer got there first. The same
    request identity is reused — a refused plan journals nothing."""
    last = None
    for _ in range(ATTEMPTS):
        rev = braincomments.revision_of(braincomments.current_bytes(entry_id))
        try:
            return brainwrite.write("comment", {**payload, "request": request,
                                                "entry": entry_id, "expected": rev})
        except brainwrite.Refused as exc:
            if not exc.extra.get("stale"):
                raise
            last = exc
    raise last


# ── a person's comment, reply, resolution and re-anchoring ─────────────

PERSON = {"open", "reply", "resolve", "reopen", "reanchor"}


def write(op: str, payload: dict) -> dict:
    """One person's write; the page's `expected` guards it. Launches
    nothing."""
    if op not in PERSON:
        raise Refused(f"unknown comment action {op!r}")
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    body = {k: v for k, v in payload.items() if k != "op"}
    if op == "open":
        body["mode"] = "comment"
    if op == "resolve":
        body["by"] = "person"
    for key in ("draft", "change", "outcome", "why", "anchor", "revision"):
        body.pop(key, None)
    try:
        result = brainwrite.write("comment", {**body, "op": op})
    except brainwrite.Refused as exc:
        raise _refused(exc) from exc
    state.broadcast({"type": "brain"})
    return {"operation": result, "comments": reading(payload.get("entry"))}


# ── Ask agent ──────────────────────────────────────────────────────────


def _instruction(entry: dict, anchor: dict, text: str) -> str:
    quote = anchor.get("quote") or ""
    where = (f"section “{anchor.get('section') or 'Opening'}”, lines "
             f"{(anchor.get('lineStart') or 0) + 1}–{(anchor.get('lineEnd') or 0) + 1} "
             f"of the body")
    said = (f"{text}\n\nThis is about one passage of {entry['id']} ({where}, at revision "
            f"{entry['revision'][:16]}):\n\n> " + quote.replace("\n", "\n> ") +
            "\n\nChange that passage, and only what the instruction needs beyond it; keep "
            "the rest of the entry as it is.")
    return said[:braindraft.MAX_REQUEST]


def ask(payload: dict) -> dict:
    """Ask the Brain Agent about a passage: a new thread (`quote`, …) or a
    follow-up on an open one (`thread`). Writes the thread's event, starts
    at most one drafting request, links it. Refused with nothing launched
    when the passage cannot be placed exactly in the entry as it is now."""
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    request = payload.get("request")
    if not isinstance(request, str) or not brainwrite.REQUEST_RE.match(request):
        raise Refused("name the request identity (8–80 letters, digits or ._:-), so a "
                      "repeat answers rather than asking twice")
    entry_id = payload.get("entry")
    try:
        entry = brain.entry(entry_id)
    except brain.NotAnId as exc:
        raise Refused(str(exc)) from exc
    with LOCK:
        events = _events(entry_id)
        found = braincomments.threads(events)
        thread = payload.get("thread")
        if thread:
            # a follow-up on an open thread: its passage must still be exact
            t = found.get(thread)
            if t is None:
                raise Refused("no such thread on this entry", 404, code="no-thread")
            linked = next((e for e in t["links"]
                           if e.get("request") == derived(request, "l")), None)
            if linked is None:
                if t["state"] != "open":
                    raise Refused("this thread is resolved — reopen it to ask again", 409,
                                  code="resolved")
                where = braincomments.locate(t["anchor"], entry)
                if where["state"] not in ("current", "relocated"):
                    raise Refused("this thread's passage cannot be placed exactly in the "
                                  f"entry now ({where.get('why')}) — re-anchor it first; "
                                  "nothing was launched", 409, code="anchor-" + where["state"])
                if payload.get("entryRevision") != entry.get("revision"):
                    raise Refused("the entry changed since this page read it — read it "
                                  "again; nothing was launched", 409, stale=True,
                                  code="entry-changed")
                anchor = dict(t["anchor"], revision=entry["revision"],
                              **{k: where[k] for k in ("lineStart", "lineEnd", "start", "end",
                                                       "section")})
                text = braincomments.clean(payload.get("text"), "the instruction",
                                           braincomments.MAX_TEXT)
                # the follow-up is said in the thread first, as the person's
                try:
                    brainwrite.write("comment", {"request": request, "entry": entry_id,
                                                 "expected": payload.get("expected"),
                                                 "op": "reply", "thread": thread,
                                                 "text": text})
                except brainwrite.Refused as exc:
                    raise _refused(exc) from exc
        else:
            thread = braincomments.thread_id(request)
            if thread not in found:
                try:
                    brainwrite.write("comment", {
                        "request": request, "entry": entry_id,
                        "expected": payload.get("expected"), "op": "open", "mode": "ask",
                        "entryRevision": payload.get("entryRevision"),
                        "quote": payload.get("quote"), "prefix": payload.get("prefix"),
                        "suffix": payload.get("suffix"), "text": payload.get("text")})
                except brainwrite.Refused as exc:
                    raise _refused(exc) from exc
                found = braincomments.threads(_events(entry_id))
            t = found[thread]
            anchor, text = t["anchor"], t["opened"].get("text") or ""
            linked = next((e for e in t["links"]), None)
        result = {"thread": thread}
        if linked is not None:
            result.update(draft=linked.get("draft"), duplicate=True)
        else:
            try:
                started = braindraft.start({"request": derived(request, "d"),
                                            "text": _instruction(entry, anchor, text),
                                            "target": entry_id,
                                            "expected": entry.get("revision")})
            except braindraft.Refused as exc:
                # the comment is kept; the request that could not start is said
                _append(entry_id, derived(request, "o"), {
                    "op": "outcome", "thread": thread, "outcome": "not-launched",
                    "why": str(exc)[:600]})
                state.broadcast({"type": "brain"})
                raise Refused(f"the comment is saved, and the agent request could not "
                              f"start: {exc}", exc.status, thread=thread,
                              **{k: v for k, v in exc.extra.items() if k != "thread"})
            draft = started["draft"]["id"]
            _append(entry_id, derived(request, "l"), {
                "op": "link", "thread": thread, "draft": draft, "text": text,
                "anchor": anchor})
            result.update(draft=draft, duplicate=bool(started.get("duplicate")))
    state.broadcast({"type": "brain"})
    result["comments"] = reading(entry_id)
    return result


# ── settling a thread against what its request did ─────────────────────


def _touches(change: dict, start: int, end: int) -> bool:
    """Whether an applied change's edits touch lines start..end of the base
    it was made from (an insertion at the passage's edge counts)."""
    for h in ((change.get("body") or {}).get("hunks") or []):
        edits = h.get("edits") or [{"beforeAt": h.get("beforeAt") or 0,
                                    "removed": h.get("removed") or 0}]
        for e in edits:
            at, gone = int(e.get("beforeAt") or 0), int(e.get("removed") or 0)
            if gone and at <= end and at + gone - 1 >= start:
                return True
            if not gone and start <= at <= end + 1:
                return True
    return False


def settle(draft_id: str) -> list[dict]:
    """Record what a drafting request came to on every thread linked to
    it. Idempotent: each outcome is written once per request and state."""
    try:
        draft = braindraft.get(draft_id)
    except braindraft.Refused:
        return []
    entry_id = draft.get("target")
    if not entry_id:
        return []
    written = []
    with LOCK:
        try:
            found = braincomments.threads(_events(entry_id))
        except braincomments.Invalid:
            return []
        for t in found.values():
            link = next((e for e in t["links"] if e.get("draft") == draft_id), None)
            if link is None:
                continue
            status = draft.get("status")
            key = link.get("request") or draft_id
            if status == "applied":
                if t["state"] == "resolved" or any(o.get("draft") == draft_id
                                                   for o in t["outcomes"]
                                                   if o.get("outcome") in ("unrelated",
                                                                           "unverified")):
                    continue
                try:
                    view = braindraft.change_view(draft_id)
                except braindraft.Refused:
                    view = {}
                change = view.get("change") or {}
                if not change.get("exact"):
                    out = ("unverified", "the edit was applied, and no exact change record "
                           "says which lines it changed — resolve this comment by hand if "
                           "it addressed it")
                elif change.get("baseRevision") != link.get("revision"):
                    out = ("unverified", "the edit was made from a later revision of the "
                           "entry than the passage was asked about, so whether it changed "
                           "this passage is not established — resolve it by hand if it did")
                elif not _touches(change, link.get("lineStart", 0), link.get("lineEnd", 0)):
                    out = ("unrelated", "the edit was applied and did not change this "
                           "passage, so the comment stays open")
                else:
                    written.append(_append(entry_id, derived(key, draft_id, "r"), {
                        "op": "resolve", "thread": t["id"], "by": "edit",
                        "draft": draft_id, "change": change.get("id"),
                        "revision": change.get("resultRevision"),
                        "text": f"resolved by the applied edit {change.get('id')}"}))
                    continue
                written.append(_append(entry_id, derived(key, draft_id, out[0]), {
                    "op": "outcome", "thread": t["id"], "draft": draft_id,
                    "outcome": out[0], "why": out[1]}))
                continue
            conflict = draft.get("conflict") or {}
            if status in ("cancelled", "discarded"):
                out = (status, f"the agent request was {status}; the entry is unchanged")
            elif conflict.get("code"):
                out = ("conflict", "Apply was refused and nothing was written "
                       f"({conflict['code']}): " + str(conflict.get("why") or
                                                      "a source or the entry changed"))
            else:
                continue
            if out[0] != "conflict" and (draft_id, out[0]) in {
                    (o.get("draft"), o.get("outcome")) for o in t["outcomes"]}:
                continue
            tag = out[0] + (("." + str(conflict.get("at") or "")[-8:].replace(":", ""))
                            if out[0] == "conflict" else "")
            written.append(_append(entry_id, derived(key, draft_id, tag), {
                "op": "outcome", "thread": t["id"], "draft": draft_id,
                "outcome": out[0], "why": out[1][:600]}))
    if written:
        state.broadcast({"type": "brain"})
    return written


def reconcile() -> int:
    """At board start, after the drafting records are recovered: settle
    every request a thread on this board is linked to."""
    n = 0
    try:
        entries = [e["id"] for e in brain.reading().get("entries") or []]
    except Exception:
        return 0
    for entry_id in entries:
        try:
            found = braincomments.threads(_events(entry_id))
        except braincomments.Invalid:
            continue
        for t in found.values():
            for link in t["links"]:
                try:
                    n += len(settle(link.get("draft") or ""))
                except (brainwrite.Refused, braincomments.Invalid):
                    continue
    return n


# ── reading ────────────────────────────────────────────────────────────


def reading(entry_id) -> dict:
    """An entry's threads, each linked request with its live state on this
    board. A read: nothing is settled, written or launched."""
    try:
        out = braincomments.reading(entry_id)
    except braincomments.Invalid as exc:
        raise Refused(str(exc), exc.status, **exc.extra) from exc
    for t in out["threads"]:
        for link in t["links"]:
            try:
                d = braindraft.get(link.get("draft") or "")
            except braindraft.Refused:
                link["live"] = {"state": "not-on-this-board",
                                "why": "the agent request was made on another board, or "
                                       "its private record is gone"}
                continue
            link["live"] = {"state": d.get("status"), "round": d.get("round"),
                            "error": (d.get("error") or {}).get("why")
                            if isinstance(d.get("error"), dict) else d.get("error"),
                            "conflict": (d.get("conflict") or {}).get("code"),
                            "change": d.get("change") if d.get("status") == "applied"
                            else None}
    return out
