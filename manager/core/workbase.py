"""A card's work-base decision, read back off the card (task 150).

Which code a card's implementation starts from is decided once, before
anything is provisioned, and written onto the card as a `base` record in
its `## Orchestration record` section (`decisions` writes it; `basechoice`
decides it). The record travels with the card — a sync, a restart and a
replica all read the same decision — and this module is the one reader the
lower layers use: `taskfiles.read_task` attaches the reading to every card,
`taskfiles.weave_stacks` and `plans.declared` read it from there.

Pure: it parses text it is handed and imports no other core module, so a
record shaped like one anywhere outside the section is never read, and a
key the contract does not name never reaches a reader.

The reading has two halves, and the difference is the whole point:

- `accepted` — the newest record whose decision is one a launch may act
  on: `stacked` (build on a Review card's pinned commit), `normal` (the
  application base), `explicit` (the card's own header governs) or
  `integration` (a phase member's branch governs).
- `latest` — the newest record of any kind. When it is `stale` or
  `blocked`, the accepted decision no longer licenses a *new* start: a
  plan refuses rather than silently choosing another base. Work already
  under way keeps its recorded plan, which this never rewrites.
"""

from __future__ import annotations

import json
import re

VERSION = 1
HEADING = "Orchestration record"
DECISIONS = ("stacked", "normal", "explicit", "integration", "blocked", "stale")
ACCEPTED = ("stacked", "normal", "explicit", "integration")

_SECTION_RE = re.compile(rf"^##\s+{re.escape(HEADING)}[ \t]*$.*?(?=^##\s|\Z)",
                         re.MULTILINE | re.DOTALL)
_RECORD_RE = re.compile(r"^<!-- bench:orchestration (\{.*\}) -->[ \t]*$", re.MULTILINE)
_ID_RE = re.compile(r"^[a-z]-[0-9a-f]{10}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
_WORD_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f‎‏‪-‮⁦-⁩]")

# Per field, how it is read back. Anything else in a record is dropped.
FIELDS = {
    "id": "id", "at": "text", "actor": "text", "source": "word",
    "card": "text", "decision": "word", "code": "word",
    "branch": "ref", "commit": "sha", "appBase": "ref", "prBase": "ref",
    "parent": "text", "parentFile": "text", "parentHead": "sha",
    "parentPr": "text", "summary": "text", "alternatives": "text",
    "inputs": "word", "supersedes": "id",
}


def _field(kind: str, value):
    if not isinstance(value, str):
        return None
    if kind == "id":
        return value if _ID_RE.match(value) else None
    if kind == "sha":
        return value if _SHA_RE.match(value) else None
    if kind == "word":
        return value if _WORD_RE.match(value) else None
    if kind == "ref":
        if not _REF_RE.match(value) or ".." in value or value.startswith("-"):
            return None
        return value
    return _UNSAFE.sub("", value)[:2000]


def _public(meta: dict) -> dict | None:
    if meta.get("v") != VERSION or meta.get("kind") != "base":
        return None
    out = {}
    for key, kind in FIELDS.items():
        value = _field(kind, meta.get(key))
        if value is not None:
            out[key] = value
    if not out.get("id") or out.get("decision") not in DECISIONS:
        return None
    if out["decision"] == "stacked" and not (out.get("branch") and out.get("commit")):
        return None          # a stacked decision names what it builds on, or is not one
    return out


def records(history: str) -> list[dict]:
    """Every `base` record in the history half's Orchestration record
    section, in file order (newest last — the board only appends)."""
    found = _SECTION_RE.search(history or "")
    if not found:
        return []
    out = []
    for match in _RECORD_RE.finditer(found.group(0)):
        try:
            meta = json.loads(match.group(1))
        except ValueError:
            continue
        public = _public(meta) if isinstance(meta, dict) else None
        if public is not None:
            out.append(public)
    return out


def reading(history: str) -> dict | None:
    """`{"accepted": …, "latest": …, "count": n}` or None for a card with no
    work-base decision at all — which reads exactly as every card did."""
    found = records(history)
    if not found:
        return None
    accepted = next((r for r in reversed(found) if r["decision"] in ACCEPTED), None)
    return {"accepted": accepted, "latest": found[-1], "count": len(found)}


def governs_new_start(found: dict | None) -> tuple[dict | None, str | None]:
    """What a *new* start may act on: (the accepted decision, or None) and,
    when the newest record withdrew it, why a new start must refuse."""
    if not found:
        return None, None
    latest = found["latest"]
    if latest["decision"] in ("stale", "blocked"):
        why = latest.get("summary") or latest["decision"]
        return found.get("accepted"), (
            f"the work-base decision for this card is {latest['decision']} "
            f"({latest.get('code') or 'reassess'}): {why}")
    return found.get("accepted"), None
