"""Brain changes (task 209): the exact difference one write made to an
entry, and where that difference is in the entry now.

A Brain Agent proposal is reviewed against the exact revision it was
drafted from, and once a person applies it the board keeps a bounded
**change record**: the request and round that proposed it, the write
operation that saved it, the base and resulting revisions, the header
fields that changed and the body's hunks — each with the section it is in,
its added and removed lines and the unchanged lines around it. The entry's
bytes stay the one authority: the record is read-only, retained beside the
private draft, and never a second writable history.

`locate` answers the reader's question — where is this change in the
entry *now* — conservatively. While the entry still holds the revision the
write produced, every hunk is exactly where the record says. After a later
edit a hunk is placed only where its own lines, with their surrounding
context, occur exactly once in the current text; repeated text, an edited
passage or a deleted section reads `unresolved`, and the reader is shown
the historical diff instead of a highlight on text that is not the change.

Stdlib only; imports no core module. Reading or computing here writes and
launches nothing.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re

CONTRACT = 1
KIND = "bench-brain-change"

CONTEXT = 2               # unchanged lines kept either side of a change
MAX_LINES = 6000          # lines of one side read into a diff
MAX_HUNKS = 120           # hunks kept in a record; the rest are counted
MAX_HUNK_LINES = 400      # lines kept in one hunk; the rest are counted
MAX_LINE = 2000           # characters kept of one line
MAX_FIELD = 500           # characters kept of a changed header field
FIELDS = ("title", "summary", "kind")

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def lines_of(text: str | None) -> list[str]:
    """A body's lines as the diff reads them: newlines normalised, the
    empty body no lines at all."""
    if not text:
        return []
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _fenced(lines: list[str]) -> list[bool]:
    """Per line, whether it sits inside (or opens or closes) a code fence:
    a heading-shaped line there is code, not a section."""
    out, opener = [], None
    for line in lines:
        m = _FENCE.match(line)
        if opener is None and m:
            opener = m.group(1)[0]
            out.append(True)
        elif opener is not None:
            out.append(True)
            if m and m.group(1)[0] == opener:
                opener = None
        else:
            out.append(False)
    return out


def sections(lines: list[str]) -> list[tuple[int, int, str]]:
    """Every heading outside a fence: (line index, level, text)."""
    fenced = _fenced(lines)
    found = []
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        m = _HEADING.match(line)
        if m:
            found.append((i, len(m.group(1)), m.group(2)))
    return found


def section_at(heads: list[tuple[int, int, str]], index: int) -> str:
    """The heading a line falls under; before any heading, the opening."""
    name = "Opening"
    for at, _level, text in heads:
        if at > index:
            break
        name = text or "Untitled section"
    return name


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE else line[:MAX_LINE] + " …"


def diff(before: str | None, after: str | None) -> dict:
    """The line difference from `before` to `after`, grouped into hunks of
    the changed lines and `CONTEXT` unchanged lines either side.

    Each hunk says where it starts on both sides (0-based line indexes),
    which section it is in, its lines as `[op, text]` (`=` unchanged, `-`
    removed, `+` added), how many lines it adds and removes, its kind
    (`insert`, `delete`, `replace`) and, for a heading replaced by
    another at the same level, the rename. Bounded: `truncated` says when
    lines, hunks or a hunk's lines were left out."""
    a_all, b_all = lines_of(before), lines_of(after)
    truncated = len(a_all) > MAX_LINES or len(b_all) > MAX_LINES
    a, b = a_all[:MAX_LINES], b_all[:MAX_LINES]
    heads_b, heads_a = sections(b), sections(a)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    hunks, omitted = [], 0
    for group in matcher.get_grouped_opcodes(CONTEXT):
        if len(hunks) >= MAX_HUNKS:
            omitted += 1
            continue
        rows: list[list[str]] = []
        added = removed = 0
        first_change_b = first_change_a = None
        renamed = []
        touches: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows.extend(["=", _clip(x)] for x in a[i1:i2])
                continue
            if first_change_b is None:
                first_change_b, first_change_a = j1, i1
            # every section a change falls in: the new text's, and the old
            # text's for what was removed
            # a blank line names nothing: it belongs to whichever side it sits on
            named = [section_at(heads_b, j) for j in range(j1, j2) if b[j].strip()] if b else []
            if i2 > i1 and a:
                # removed lines are named by the section they were removed from
                named += [section_at(heads_a, i1)]
            if not named:
                named = [section_at(heads_b, j1) if b else "Opening"]
            for name in named:
                if name not in touches:
                    touches.append(name)
            gone, came = a[i1:i2], b[j1:j2]
            rows.extend(["-", _clip(x)] for x in gone)
            rows.extend(["+", _clip(x)] for x in came)
            removed += len(gone)
            added += len(came)
            if tag == "replace":
                old = [(len(m.group(1)), m.group(2)) for m in map(_HEADING.match, gone) if m]
                new = [(len(m.group(1)), m.group(2)) for m in map(_HEADING.match, came) if m]
                for (lo, to), (ln, tn) in zip(old, new):
                    if lo == ln and to != tn:
                        renamed.append({"from": to[:MAX_FIELD], "to": tn[:MAX_FIELD]})
        # the change itself, apart from its context
        lead = next(k for k, r in enumerate(rows) if r[0] != "=")
        tail = max(k for k, r in enumerate(rows) if r[0] != "=")
        kept_rows = rows
        dropped = 0
        if len(rows) > MAX_HUNK_LINES:
            dropped = len(rows) - MAX_HUNK_LINES
            kept_rows = rows[:MAX_HUNK_LINES]
            truncated = True
        after_at = group[0][3] + lead if lead else group[0][3]
        before_at = group[0][1] + lead if lead else group[0][1]
        kind = "insert" if not removed else "delete" if not added else "replace"
        hunks.append({
            "n": len(hunks) + 1,
            "kind": kind,
            "section": touches[0] if touches else "Opening",
            "sections": touches,
            "beforeSection": section_at(heads_a, first_change_a or 0) if a else None,
            "beforeAt": first_change_a if first_change_a is not None else before_at,
            "afterAt": first_change_b if first_change_b is not None else after_at,
            "added": added, "removed": removed,
            "contextBefore": [r[1] for r in rows[:lead]],
            "contextAfter": [r[1] for r in rows[tail + 1:]],
            "addedLines": [r[1] for r in rows if r[0] == "+"][:MAX_HUNK_LINES],
            "lines": kept_rows, "droppedLines": dropped,
            "renamed": renamed,
        })
    if omitted:
        truncated = True
    changed = []
    for h in hunks:
        for name in h["sections"]:
            if name not in changed:
                changed.append(name)
    return {"hunks": hunks, "sections": changed,
            "renamed": [r for h in hunks for r in h["renamed"]],
            "added": sum(h["added"] for h in hunks),
            "removed": sum(h["removed"] for h in hunks),
            "omittedHunks": omitted, "truncated": truncated,
            "beforeLines": len(a_all), "afterLines": len(b_all)}


def fields(before: dict | None, after: dict) -> list[dict]:
    """The header fields a write changed, as before → after."""
    out = []
    for name in FIELDS:
        was = ((before or {}).get(name) or "") if before is not None else ""
        now = after.get(name) or ""
        if was != now:
            out.append({"field": name, "before": was[:MAX_FIELD] if before is not None else None,
                        "after": now[:MAX_FIELD]})
    return out


def record(*, draft: str, round_no: int | None, request: str | None,
           entry_id: str, file: str | None, base: dict | None, result: dict | None,
           at: str, operation: str | None = None, mode: str = "update") -> dict:
    """One applied change, bounded and read-only.

    `base` and `result` are the entry as `{revision, title, summary, kind,
    body}`; `base` is None for a new entry. `result` None means the bytes
    the write produced could not be read back as that revision (another
    write landed first), so no diff is claimed: `exact` is False and the
    reader is told so."""
    exact = result is not None and (base is not None or mode == "create")
    body = diff(base.get("body") if base else "", result.get("body")) if exact else None
    out = {"v": CONTRACT, "kind": KIND, "draft": draft, "round": round_no,
           "request": request, "operation": operation or request,
           "entry": entry_id, "file": file, "mode": mode,
           "baseRevision": (base or {}).get("revision"),
           "resultRevision": (result or {}).get("revision"),
           "at": at, "exact": exact,
           "fields": fields(base, result) if exact else [],
           "body": body}
    if not exact:
        out["why"] = ("the entry this request read was not retained, so the exact "
                      "difference cannot be shown" if result is not None else
                      "the entry was changed again before its new text could be read "
                      "back, so the exact difference cannot be shown")
    out["id"] = "ch-" + hashlib.sha256(json.dumps(
        [draft, round_no, request, entry_id, out["baseRevision"], out["resultRevision"]],
        sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return out


def _occurrences(hay: list[str], needle: list[str]) -> list[int]:
    if not needle or len(needle) > len(hay):
        return []
    first, size = needle[0], len(needle)
    return [i for i in range(len(hay) - size + 1)
            if hay[i] == first and hay[i:i + size] == needle]


def locate(change: dict, current_body: str | None, *, current_revision: str | None) -> list[dict]:
    """Where each hunk of `change` is in the entry's text now.

    `current` — the entry still holds the revision the write produced, so
    the record's own positions are exact. `relocated` — it has been edited
    since, and this hunk's lines with their context occur exactly once in
    the current text. `unresolved` — anything else: the passage was edited
    or removed, or its text repeats, so no current place is claimed.

    `start`/`end` are 0-based line indexes in the current body: the added
    lines, or for a deletion `start == end`, the place the removed lines
    were."""
    body = change.get("body") if isinstance(change, dict) else None
    if not body:
        return []
    exact = (change.get("exact") and current_revision is not None
             and current_revision == change.get("resultRevision"))
    hay = lines_of(current_body)
    out = []
    for h in body.get("hunks") or []:
        added = h.get("addedLines") or []
        if exact and not h.get("droppedLines"):
            start = h.get("afterAt") or 0
            out.append({"n": h["n"], "state": "current", "start": start,
                        "end": start + h.get("added", 0)})
            continue
        before, after_ctx = h.get("contextBefore") or [], h.get("contextAfter") or []
        needle = before + added + after_ctx
        meaningful = any(x.strip() for x in needle)
        hits = _occurrences(hay, needle) if meaningful else []
        if len(hits) == 1 and (added or (before and after_ctx)):
            start = hits[0] + len(before)
            out.append({"n": h["n"], "state": "relocated", "start": start,
                        "end": start + len(added)})
        else:
            out.append({"n": h["n"], "state": "unresolved",
                        "why": ("its text occurs more than once now" if len(hits) > 1 else
                                "its passage was edited or removed since")})
    return out
