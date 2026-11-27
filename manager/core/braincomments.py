"""Comments on selected Brain text (task 210): the record, version 1, and
where each comment's passage is in the entry now.

A person selects a passage in a maintained Brain entry and comments on it,
or asks the Brain Agent to work on it. The discussion is a **thread**,
kept with the entry in the work state — one file per entry,
`brain/comments/<entry id>.md`, committed and synced like every Brain
write — as append-only records, one per event, newest last:

    <!-- bench:brain-comment {"v": 1, "kind": "open", ...} -->
    - 2026-11-27 10:00 UTC · ronald · comment — the readable line

The readable line is written from the record and never read back. Every
string is bounded, scrubbed of anything credential-shaped and stripped of
control and direction-changing characters; angle brackets are escaped in
the record, so no text can end the comment it sits in or pose as markup.
Nothing here is private: no transcript, no prompt, no path and no unread
state — those stay machine-local with the drafting request they belong to.

**The anchor is the source, not the page.** A selection arrives as the text
the reader saw and a little text either side. It is mapped onto the entry's
Markdown by its *letters and digits* alone — the one thing the page's
rendering never changes: emphasis marks, link targets, list markers, code
fences and HTML comments are left out on the source side, and punctuation
and spacing on both. The anchor records the entry's revision, the source
character and line positions, the section, the quote and the 32 letters and
digits either side. A quote that occurs more than once and that its context
does not tell apart is refused rather than attached to a guess.

**Locating is conservative** (`locate`): while the entry is the anchored
revision the recorded place is exact (`current`); after an edit the quote
is placed only where it occurs exactly once *with* its recorded context
(`relocated`); a quote whose context changed or that repeats is `outdated`,
one that is gone — or an entry retired or missing — is `detached`. Neither
is re-anchored by itself: re-anchoring is a person's explicit act.

Imports `brain`, `brainchange` and `redact`; reads files, writes nothing.
`brainwrite` writes the records; `brainthreads` asks the Brain Agent and
settles threads against applied changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

import brain
import brainchange
import redact

CONTRACT = 1
DIRECTORY = "comments"
MARK = "<!-- bench:brain-comment "
HEADER_FIELD = "**Brain comments:**"
NAME_RE = re.compile(r"^([a-z0-9][a-z0-9-]{1,63})\.md$")
THREAD_RE = re.compile(r"^t-[0-9a-f]{10}$")

MAX_TEXT = 2000           # characters of one comment, reply or note
MAX_QUOTE = 600           # characters of one selected passage
MIN_QUOTE = 3             # letters and digits a selection must hold
CONTEXT = 32              # letters and digits kept either side of a quote
MAX_FILE = 512 * 1024     # bytes of one entry's comment record
MAX_THREADS = 200         # threads on one entry
MAX_EVENTS = 2000         # events on one entry

KINDS = ("open", "reply", "resolve", "reopen", "reanchor", "link", "outcome")
# what a person writes through the browser; `link` and `outcome` are the
# board's own records of an agent request and what became of it
PERSON_KINDS = ("open", "reply", "resolve", "reopen", "reanchor")
MODES = ("comment", "ask")
OUTCOMES = ("not-launched", "failed", "cancelled", "discarded", "conflict",
            "unrelated", "unverified")
RESOLVED_BY = ("person", "edit")

_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LINK_TARGET = re.compile(r"\]\(([^)\s]*)\)")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LIST_NUMBER = re.compile(r"^(\s*)(\d+)[.)]\s", re.M)
_TASK_BOX = re.compile(r"^\s*[-*+]\s+\[([ xX])\]", re.M)


class Invalid(ValueError):
    """A comment write that cannot be made, with nothing written."""

    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


# ── where records live ─────────────────────────────────────────────────


def directory() -> Path:
    return brain.directory() / DIRECTORY


def record_name(entry_id: str) -> str:
    if not isinstance(entry_id, str) or not brain.ID_RE.match(entry_id):
        raise Invalid("not a Brain entry id")
    return f"{entry_id}.md"


def file_for(entry_id: str) -> str:
    """The record's name as the writer journals it: relative to brain/."""
    return f"{DIRECTORY}/{record_name(entry_id)}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def thread_id(seed: str) -> str:
    """A thread's identity, derived from the request that opened it, so the
    same request again finds the thread it already made."""
    return "t-" + hashlib.sha256(f"thread:{seed}".encode("utf-8")).hexdigest()[:10]


def event_id(seed: str) -> str:
    return "c-" + hashlib.sha256(f"event:{seed}".encode("utf-8")).hexdigest()[:10]


# ── text: bounded, scrubbed, inert ─────────────────────────────────────


def clean(value, what: str, cap: int = MAX_TEXT, *, required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Invalid(f"{what} must be text", field=what)
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if _UNSAFE.search(text):
        raise Invalid(f"{what} contains control or direction-changing characters",
                      field=what)
    if required and not text:
        raise Invalid(f"{what} is required", field=what)
    if len(text) > cap:
        raise Invalid(f"{what} is at most {cap} characters", field=what)
    return redact.scrub(text, "«redacted»")


# ── the source skeleton: letters and digits, mapped to the Markdown ────


def _masked(body: str) -> list[bool]:
    """Per source character, whether rendering drops it: code-fence lines,
    link targets, HTML comments, ordered-list numbers and task boxes."""
    mask = [False] * len(body)

    def hide(a: int, b: int) -> None:
        for k in range(max(0, a), min(len(body), b)):
            mask[k] = True

    at = 0
    for line in body.split("\n"):
        if _FENCE.match(line):
            hide(at, at + len(line))
        at += len(line) + 1
    for m in _LINK_TARGET.finditer(body):
        hide(m.start(1), m.end(1))
    for m in _HTML_COMMENT.finditer(body):
        hide(m.start(), m.end())
    for m in _LIST_NUMBER.finditer(body):
        hide(m.start(2), m.end(2))
    for m in _TASK_BOX.finditer(body):
        hide(m.start(1), m.end(1))
    return mask


def skeleton(text: str, *, source: bool = False) -> tuple[str, list[int]]:
    """The letters and digits of `text`, and each one's index in it. With
    `source`, the characters rendering drops are left out too."""
    text = text or ""
    mask = _masked(text) if source else None
    chars, where = [], []
    for k, c in enumerate(text):
        if c.isalnum() and not (mask and mask[k]):
            chars.append(c)
            where.append(k)
    return "".join(chars), where


def _find_all(hay: str, needle: str) -> list[int]:
    out, k = [], hay.find(needle)
    while k != -1 and needle:
        out.append(k)
        k = hay.find(needle, k + 1)
    return out


_BLANK = re.compile(r"\n[ \t]*\n")


def _context(body: str, s: str, where: list[int], i: int, n: int) -> tuple[str, str]:
    """The letters and digits either side of a match, within its own block
    (a paragraph, a list, a table): text across a blank line is another
    passage, which a section move or an edit nearby may change freely."""
    start, end = where[i], where[i + n - 1] + 1
    lo = max((m.end() for m in _BLANK.finditer(body, 0, start)), default=0)
    nxt = _BLANK.search(body, end)
    hi = nxt.start() if nxt else len(body)
    j = i
    while j > 0 and where[j - 1] >= lo and i - (j - 1) <= CONTEXT:
        j -= 1
    k = i + n
    while k < len(s) and where[k] < hi and k - (i + n) < CONTEXT:
        k += 1
    return s[j:i], s[i + n:k]


def _place(body: str, skel_where: list[int], i: int, n: int) -> dict:
    start = skel_where[i]
    end = skel_where[i + n - 1] + 1
    lines = brainchange.lines_of(body)
    line_start = body.count("\n", 0, start)
    line_end = body.count("\n", 0, end - 1 if end > 0 else 0)
    section = brainchange.section_at(brainchange.sections(lines), line_start)
    return {"start": start, "end": end, "lineStart": line_start, "lineEnd": line_end,
            "section": section[:200]}


def anchor(body: str, revision: str, quote, prefix=None, suffix=None) -> dict:
    """Map a selection onto the entry's Markdown, or refuse it.

    `quote` is the text the reader selected; `prefix` and `suffix` the text
    the page showed just before and after it, used only to tell repeated
    quotes apart. Raises `Invalid` (`no-match`, `ambiguous`, `too-short`)
    with nothing written."""
    shown = clean(quote, "the selected text", MAX_QUOTE)
    shown = re.sub(r"\s+", " ", shown)
    q, _ = skeleton(shown)
    if len(q) < MIN_QUOTE:
        raise Invalid("select at least a few letters or digits of the entry's text",
                      code="too-short")
    s, where = skeleton(body, source=True)
    hits = _find_all(s, q)
    if not hits:
        raise Invalid("the selected text is not in this entry's text as it is saved — "
                      "select text inside the entry's body (not the page around it)",
                      409, code="no-match")
    if len(hits) > 1:
        p = skeleton(prefix if isinstance(prefix, str) else "")[0][-CONTEXT:]
        x = skeleton(suffix if isinstance(suffix, str) else "")[0][:CONTEXT]
        if p or x:
            told = [i for i in hits
                    if (not p or s[:i].endswith(p)) and (not x or s[i + len(q):].startswith(x))]
        else:
            told = []
        if len(told) != 1:
            raise Invalid(f"the selected text occurs {len(hits)} times in this entry and "
                          "the text around it does not say which — select a longer "
                          "passage", 409, code="ambiguous", occurrences=len(hits))
        hits = told
    i = hits[0]
    before, after = _context(body, s, where, i, len(q))
    out = {"revision": revision, "quote": shown, "before": before, "after": after}
    out.update(_place(body, where, i, len(q)))
    return out


def locate(anchor_: dict | None, entry: dict | None) -> dict:
    """Where an anchor's passage is in the entry now; see the module
    docstring for the four states. A read."""
    if not anchor_:
        return {"state": "detached", "why": "this comment has no recorded passage"}
    state = (entry or {}).get("state")
    if state == "tombstone":
        return {"state": "detached", "why": "the entry was retired"}
    if state != "entry":
        return {"state": "detached", "why": "the entry is not on this board"}
    body = entry.get("body") or ""
    if entry.get("revision") == anchor_.get("revision"):
        return {"state": "current", **{k: anchor_.get(k) for k in
                                         ("start", "end", "lineStart", "lineEnd", "section")}}
    q, _ = skeleton(anchor_.get("quote") or "")
    if len(q) < MIN_QUOTE:
        return {"state": "detached", "why": "the recorded passage holds no text to find"}
    s, where = skeleton(body, source=True)
    hits = _find_all(s, q)
    ctx = (anchor_.get("before") or "", anchor_.get("after") or "")
    told = [i for i in hits if _context(body, s, where, i, len(q)) == ctx]
    if len(told) == 1:
        return {"state": "relocated", **_place(body, where, told[0], len(q))}
    if not hits:
        return {"state": "detached",
                "why": "the passage was edited or removed since it was commented on"}
    if len(told) > 1 or len(hits) > 1:
        return {"state": "outdated",
                "why": f"its text now occurs {len(hits)} times, and the text around it "
                       "does not say which — re-anchor it to the right passage"}
    return {"state": "outdated",
            "why": "the text around it changed since it was commented on — check it "
                   "and re-anchor it, or resolve the comment"}


def anchor_public(a: dict | None) -> dict | None:
    if not a:
        return None
    return {k: a.get(k) for k in ("revision", "quote", "start", "end", "lineStart",
                                  "lineEnd", "section", "before", "after")}


# ── the record ─────────────────────────────────────────────────────────

_STR = {"id", "thread", "kind", "at", "actor", "actorSource", "text", "mode",
        "draft", "request", "outcome", "why", "by", "change", "revision", "operation"}


def _anchor_in(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in ("revision", "quote", "before", "after", "section"):
        v = raw.get(key)
        if isinstance(v, str):
            out[key] = v[:MAX_QUOTE] if key == "quote" else v[:200]
    for key in ("start", "end", "lineStart", "lineEnd"):
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10 ** 7:
            out[key] = v
    return out if out.get("quote") and out.get("revision") else None


def _event_in(raw) -> dict | None:
    """One record read field by field: an unknown key never reaches a
    reader, and a record missing its identity is not an event."""
    if not isinstance(raw, dict) or raw.get("v") != CONTRACT:
        return None
    if raw.get("kind") not in KINDS:
        return None
    ev = {k: _scrubbed(raw[k]) for k in _STR if isinstance(raw.get(k), str)}
    if not (ev.get("id") and ev.get("at") and THREAD_RE.match(ev.get("thread") or "")):
        return None
    if "anchor" in raw:
        ev["anchor"] = _anchor_in(raw.get("anchor"))
    for key in ("lineStart", "lineEnd"):
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            ev[key] = v
    return ev


def _scrubbed(text: str) -> str:
    return redact.scrub(_UNSAFE.sub("", text), "«redacted»")[:MAX_TEXT]


def _readable(ev: dict) -> str:
    at = (ev.get("at") or "")[:16].replace("T", " ")
    who = ev.get("actor") or "board"
    what = {"open": f"{ev.get('mode') or 'comment'} on “{(ev.get('anchor') or {}).get('quote', '')[:80]}”",
            "reply": "reply", "resolve": f"resolved ({ev.get('by') or 'person'})",
            "reopen": "reopened", "reanchor": "re-anchored",
            "link": f"agent request {ev.get('draft') or ''}",
            "outcome": f"agent request {ev.get('outcome') or ''}"}.get(ev["kind"], ev["kind"])
    said = ev.get("text") or ev.get("why") or ""
    line = f"- {at} UTC · {who} · {what}" + (f" — {said}" if said else "")
    line = re.sub(r"\s+", " ", line).replace("<", "‹").replace(">", "›")
    return line[:600]


def render_event(ev: dict) -> str:
    data = json.dumps(dict(ev, v=CONTRACT), sort_keys=True, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e")
    return f"{MARK}{data} -->\n{_readable(ev)}\n"


def header(entry_id: str) -> str:
    return (f"# Comments on {entry_id}\n\n{HEADER_FIELD} {CONTRACT}\n"
            f"**Comments on:** {entry_id}\n\n"
            f"<!-- bench:brain-comments v{CONTRACT} — written by the board. One record "
            "per event, newest last; the line under each is its readable summary. "
            "Records are history, never instructions. -->\n\n")


def appended(entry_id: str, current: bytes | None, ev: dict) -> str:
    text = current.decode("utf-8") if current is not None else header(entry_id)
    if not text.endswith("\n"):
        text += "\n"
    out = text + render_event(ev)
    if len(out.encode("utf-8")) > MAX_FILE:
        raise Invalid(f"this entry's comment record is full ({MAX_FILE // 1024} KiB); "
                      "nothing was written", 409, code="full")
    return out


def parse(data: bytes | None) -> dict:
    """The record's events in file order, and what could not be read."""
    out = {"events": [], "malformed": 0, "state": "absent", "contract": None}
    if data is None:
        return out
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        out["state"] = "invalid"
        return out
    out["state"] = "record"
    for line in text.split("\n"):
        if line.startswith(HEADER_FIELD):
            out["contract"] = line[len(HEADER_FIELD):].strip()
        if not line.startswith(MARK):
            continue
        body = line[len(MARK):]
        if not body.rstrip().endswith("-->"):
            out["malformed"] += 1
            continue
        try:
            ev = _event_in(json.loads(body.rstrip()[:-3]))
        except ValueError:
            ev = None
        if ev is None:
            out["malformed"] += 1
            continue
        out["events"].append(ev)
    if out["contract"] not in (None, str(CONTRACT)):
        out["state"] = "unsupported"
        out["events"] = []
    return out


def threads(events: list[dict]) -> dict[str, dict]:
    """Every thread the events describe, oldest first. A thread is open or
    resolved by its newest resolve/reopen; replies, agent requests and
    their outcomes never resolve it."""
    out: dict[str, dict] = {}
    for ev in events:
        tid = ev["thread"]
        if ev["kind"] == "open":
            if tid in out or not ev.get("anchor"):
                continue
            out[tid] = {"id": tid, "mode": ev.get("mode") if ev.get("mode") in MODES
                        else "comment", "opened": ev, "anchor": ev["anchor"],
                        "state": "open", "messages": [ev], "links": [], "outcomes": [],
                        "resolution": None, "history": [ev]}
            continue
        t = out.get(tid)
        if t is None:
            continue
        t["history"].append(ev)
        if ev["kind"] == "reply":
            t["messages"].append(ev)
        elif ev["kind"] == "reanchor" and ev.get("anchor"):
            t["anchor"] = ev["anchor"]
        elif ev["kind"] == "link":
            t["links"].append(ev)
        elif ev["kind"] == "outcome":
            t["outcomes"].append(ev)
        elif ev["kind"] == "resolve":
            t["state"], t["resolution"] = "resolved", ev
        elif ev["kind"] == "reopen":
            t["state"], t["resolution"] = "open", None
    return out


def _read_regular(path: Path) -> bytes | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise Invalid(f"brain/{DIRECTORY}/{path.name} is not a regular file; "
                      "bench reads and writes no link", 409)
    if info.st_size > MAX_FILE:
        raise Invalid(f"brain/{DIRECTORY}/{path.name} is over {MAX_FILE // 1024} KiB", 409)
    with open(path, "rb") as fh:
        return fh.read(MAX_FILE + 1)


def current_bytes(entry_id: str) -> bytes | None:
    d = directory()
    try:
        info = os.lstat(d)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise Invalid(f"brain/{DIRECTORY}/ is not a plain directory; bench reads no link", 409)
    return _read_regular(d / record_name(entry_id))


def revision_of(data: bytes | None) -> str:
    return brain.revision(data) if data is not None else "none"


# ── one entry's comments, read ─────────────────────────────────────────


def _public_event(ev: dict) -> dict:
    out = {k: ev.get(k) for k in ("id", "kind", "at", "actor", "actorSource", "text",
                                  "mode", "draft", "outcome", "why", "by", "change",
                                  "revision", "lineStart", "lineEnd") if ev.get(k) is not None}
    if ev.get("anchor"):
        out["anchor"] = anchor_public(ev["anchor"])
    return out


def reading(entry_id: str) -> dict:
    """An entry's threads with where each passage is now. A read: it writes,
    fetches and launches nothing."""
    name = record_name(entry_id)
    try:
        entry = brain.entry(entry_id)
    except brain.NotAnId as exc:
        raise Invalid(str(exc)) from exc
    data = current_bytes(entry_id)
    parsed = parse(data)
    found = threads(parsed["events"])
    out_threads = []
    for t in list(found.values())[:MAX_THREADS]:
        where = locate(t["anchor"], entry)
        out_threads.append({
            "id": t["id"], "mode": t["mode"], "state": t["state"],
            "anchor": anchor_public(t["anchor"]), "location": where,
            "opened": _public_event(t["opened"]),
            "messages": [_public_event(e) for e in t["messages"]],
            "links": [_public_event(e) for e in t["links"]],
            "outcomes": [_public_event(e) for e in t["outcomes"]],
            "resolution": _public_event(t["resolution"]) if t["resolution"] else None,
            "history": [_public_event(e) for e in t["history"]]})
    st = entry.get("state")
    return {"v": CONTRACT, "entry": {"id": entry_id, "state": st,
                                     "revision": entry.get("revision"),
                                     "title": entry.get("title")},
            "file": f"brain/{DIRECTORY}/{name}", "revision": revision_of(data),
            "state": parsed["state"], "malformed": parsed["malformed"],
            "commentable": st == "entry",
            "why": None if st == "entry" else
            ("a retired entry is kept as it is; its comments stay readable" if st == "tombstone"
             else "only a maintained Brain entry can be commented on"),
            "threads": out_threads,
            "counts": {"open": sum(1 for t in out_threads if t["state"] == "open"),
                       "resolved": sum(1 for t in out_threads if t["state"] == "resolved")}}


# ── a new event, validated against the record and the entry ────────────


def build(op: str, payload: dict, *, entry: dict, events: list[dict], actor: str,
          actor_source: str, request: str) -> dict:
    """The event one write would append, or `Invalid` with nothing written.
    Person ops are `open`, `reply`, `resolve`, `reopen`, `reanchor`; the
    board's own are `link` and `outcome` (and `resolve` by an edit)."""
    if op not in KINDS:
        raise Invalid(f"unknown comment write {op!r}")
    if entry.get("state") != "entry":
        raise Invalid("only a maintained Brain entry can be commented on — a retired "
                      "entry is kept as it is, and a filed review is discussed on its "
                      "card", 409, code="not-commentable")
    if len(events) >= MAX_EVENTS:
        raise Invalid("this entry's comment record is full; nothing was written", 409,
                      code="full")
    found = threads(events)
    ev = {"id": event_id(request), "kind": op, "at": _now(), "actor": actor,
          "actorSource": actor_source, "request": request}
    if op == "open":
        if len(found) >= MAX_THREADS:
            raise Invalid(f"this entry has {MAX_THREADS} threads, the most kept; "
                          "nothing was written", 409, code="full")
        rev = payload.get("entryRevision")
        if rev != entry.get("revision"):
            raise Invalid("the entry changed since this page read it — read it again and "
                          "select the passage again; nothing was written", 409,
                          stale=True, code="entry-changed")
        mode = payload.get("mode") or "comment"
        if mode not in MODES:
            raise Invalid("a thread is a comment or an agent request")
        ev.update(thread=thread_id(request), mode=mode,
                  text=clean(payload.get("text"), "the comment"),
                  anchor=anchor(entry.get("body") or "", entry["revision"],
                                payload.get("quote"), payload.get("prefix"),
                                payload.get("suffix")))
        return ev
    tid = payload.get("thread")
    if not isinstance(tid, str) or tid not in found:
        raise Invalid("no such thread on this entry", 404, code="no-thread")
    t = found[tid]
    ev["thread"] = tid
    if op == "reply":
        ev["text"] = clean(payload.get("text"), "the reply")
    elif op == "resolve":
        if t["state"] == "resolved":
            raise Invalid("this thread is already resolved", 409, code="already")
        by = payload.get("by") or "person"
        if by not in RESOLVED_BY:
            raise Invalid("a thread is resolved by a person or by an applied edit")
        ev["by"] = by
        ev["text"] = clean(payload.get("text"), "the note", required=(by == "person"))
        for key in ("draft", "change", "revision"):
            if isinstance(payload.get(key), str):
                ev[key] = payload[key][:80]
    elif op == "reopen":
        if t["state"] != "resolved":
            raise Invalid("this thread is open", 409, code="already")
        ev["text"] = clean(payload.get("text"), "the note")
    elif op == "reanchor":
        rev = payload.get("entryRevision")
        if rev != entry.get("revision"):
            raise Invalid("the entry changed since this page read it — select the "
                          "passage again; nothing was written", 409, stale=True,
                          code="entry-changed")
        ev["anchor"] = anchor(entry.get("body") or "", entry["revision"],
                              payload.get("quote"), payload.get("prefix"),
                              payload.get("suffix"))
        ev["text"] = clean(payload.get("text"), "the note", required=False)
    elif op == "link":
        ev["draft"] = str(payload.get("draft") or "")[:40]
        ev["text"] = clean(payload.get("text"), "the instruction")
        a = payload.get("anchor") or {}
        ev["revision"] = str(a.get("revision") or "")[:80]
        ev["lineStart"], ev["lineEnd"] = int(a.get("lineStart", 0)), int(a.get("lineEnd", 0))
    elif op == "outcome":
        if payload.get("outcome") not in OUTCOMES:
            raise Invalid("not an agent request outcome")
        ev["outcome"] = payload["outcome"]
        ev["why"] = clean(payload.get("why"), "the reason", 600)
        if isinstance(payload.get("draft"), str):
            ev["draft"] = payload["draft"][:40]
    return ev
