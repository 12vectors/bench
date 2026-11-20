"""The project's Brain, read (task 157): maintained Markdown entries in
`<work root>/brain/`, each with a stable opaque id, and the two reads the
page makes of them — the index and one entry.

The record contract is version 1, and this module is its only reader.
`AGENTS.md` ("The Brain: maintained entries, read") is the reference for
people; the short form:

    # How review reports stay on their own board

    **Brain:** 1
    **Id:** e-report-routing
    **Kind:** Guide
    **Classification:** Current
    **Summary:** One line saying what the entry explains.
    **Source:** #148, plans/148-report-isolation.md
    **Related:** e-report-isolation

    The body, in Markdown.

    ## Classification history

    - 2026-11-20 · Current · ronald — explains the shipped routing

What this module promises, and what it does not:

- **Reading is passive.** It lists one directory and reads regular files
  in it. It creates nothing (a missing `brain/` is a state, not a reason
  to make one), commits, syncs, fetches, runs no program and launches
  nothing. It asks git nothing either: a revision is a digest of the bytes
  read, so an uncommitted edit is a new revision and a commit that changed
  nothing is not.
- **Identity is the `**Id:**` line**, never the file's name, title or
  classification, so a rename, a retitle or a reclassification keeps the
  address `/brain/e/<id>`. Two files claiming one id are both refused,
  never merged; an id nothing carries is *unknown*, and an entry kept with
  `**Removed:**` is a *tombstone* — retained, and said to be removed.
- **Containment.** Only flat names directly under `brain/`, never a link
  (the directory itself included), never a file over the size cap, and
  nothing a link would carry out of the work root.
- **Versions.** A file whose `**Brain:**` names another version is listed
  as unsupported and never interpreted; one with no `**Brain:**` line is
  not an entry. A later version brings its own reader and, where records
  must change, an explicit migration written through the board's write
  path (#158) — never a rewrite on read.

Imports `config` and `taskfiles` (to say whether a card a source names is
on this board); it writes nothing anywhere.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

import config
import taskfiles

CONTRACT = 1
DIRECTORY = "brain"

KINDS = ("Review", "Investigation", "Decision", "Guide", "Prototype",
         "Attachment")
CLASSIFICATIONS = ("Current", "Historical", "Superseded", "Unclassified")

# What one entry may be, and how much of a directory is read. A request
# that would need more is told so rather than read without bound.
MAX_ENTRY_BYTES = 256 * 1024
MAX_FILES = 1000
MAX_DIAGNOSTICS = 50
MAX_LIST_ITEMS = 20
MAX_FIELD_CHARS = 500
MAX_TITLE_CHARS = 200

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md$")
FIELD_RE = re.compile(r"^\*\*([A-Za-z][A-Za-z ]{0,30}):\*\*\s*(.*?)\s*$")
TITLE_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$")
HISTORY_HEADING = "## Classification history"
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HISTORY_RE = re.compile(
    r"^-\s+(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?Z?)?)\s+·\s+"
    r"(" + "|".join(CLASSIFICATIONS) + r")\s+·\s+([^—]{1,80}?)\s+—\s+(.+?)\s*$")
CARD_REF_RE = re.compile(r"^#?(\d{1,6})$")
FILE_REF_RE = re.compile(r"^(plans|reference)/([A-Za-z0-9][A-Za-z0-9._-]{0,120})$")
URL_RE = re.compile(r"^https?://[^\s<>\"']{1,400}$")

# the fields version 1 reads; anything else is noted and ignored
FIELDS = ("Brain", "Id", "Kind", "Classification", "Summary", "Source",
          "Related", "Supersedes", "Superseded by", "Removed", "Replaced by",
          "Invalidated", "Override")
REQUIRED = ("Brain", "Id", "Kind", "Classification")

# characters that change how text reads without being text: C0/C1 control
# (bar tab and newline) and the bidirectional overrides and isolates
_INVISIBLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩‎‏]")


class NotAnId(ValueError):
    """An identifier that cannot be an entry's id, so nothing was read."""


def _clean(text: str, cap: int = MAX_FIELD_CHARS) -> str:
    text = _INVISIBLE.sub("", text).strip()
    return text if len(text) <= cap else text[:cap - 1] + "…"


def directory() -> Path:
    return config.work_root() / DIRECTORY


def revision(data: bytes) -> str:
    """An entry's revision: a digest of its bytes, so it moves exactly
    when the content does — committed or not."""
    return hashlib.sha256(data).hexdigest()[:16]


# ── one file ───────────────────────────────────────────────────────────


def _split_list(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()][:MAX_LIST_ITEMS]


def _header(lines: list[str]) -> tuple[str | None, list[tuple[str, str]], int]:
    """The title, the header's field lines in order and the index of the
    first body line. The header is the title, then one block of field
    lines (blank lines are allowed before the first). It ends at the first
    blank line or the first line that is not a field, so a body that opens
    with a bold lead-in (`**Note:** …`) is body, never a field."""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return None, [], i
    match = TITLE_RE.match(lines[i])
    title = None
    if match and not lines[i].startswith("##"):
        # a header with no title is still read, so the record can say
        # what is wrong with it rather than claim it is not one at all
        title = match.group(1)
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    fields: list[tuple[str, str]] = []
    while i < len(lines):
        field = FIELD_RE.match(lines[i])
        if not field:
            break
        fields.append((field.group(1), field.group(2)))
        i += 1
    return title, fields, i


def _fence_marks(lines: list[str]) -> list[bool]:
    """Per line, whether it is a code fence or inside one: a line opening
    with three or more backticks or tildes opens a fence, and a bare line
    of at least as many of the same character closes it. An unclosed
    fence runs to the end."""
    marks: list[bool] = []
    opener = None
    for line in lines:
        fence = FENCE_RE.match(line)
        if opener is None:
            if fence:
                opener = fence.group(1)
            marks.append(opener is not None)
            continue
        marks.append(True)
        if (fence and fence.group(1)[0] == opener[0]
                and len(fence.group(1)) >= len(opener) and not fence.group(2).strip()):
            opener = None
    return marks


def _history(body_lines: list[str]) -> tuple[list[str], list[dict], list[str]]:
    """The body without its `## Classification history` section, the
    section's records, oldest first, and the lines it could not read.

    The section is the body's last top-level section: its heading counts
    only outside a code fence, and only when no other `#` or `##` heading
    follows it outside one. So an entry that quotes the format in a fence,
    or has a section after one, keeps its whole body."""
    fenced = _fence_marks(body_lines)
    headings = [n for n, line in enumerate(body_lines)
                if not fenced[n] and (line.startswith("## ") or line.startswith("# "))]
    if not headings or body_lines[headings[-1]].strip() != HISTORY_HEADING:
        return body_lines, [], []
    start = headings[-1]
    records, unread = [], []
    for line in body_lines[start + 1:]:
        if not line.strip():
            continue
        match = HISTORY_RE.match(line.strip())
        if not match:
            unread.append(_clean(line, 120))
            continue
        records.append({"at": match.group(1),
                        "classification": match.group(2),
                        "actor": _clean(match.group(3), 80),
                        "reason": _clean(match.group(4))})
    return body_lines[:start], records, unread


def fence_marks(lines: list[str]) -> list[bool]:
    """Per line, whether it is a code fence or inside one — the same
    reading the history section is found by."""
    return _fence_marks(lines)


def fence_unclosed(lines: list[str]) -> bool:
    """Whether a code fence opened in `lines` is still open at the end."""
    opener = None
    for line in lines:
        fence = FENCE_RE.match(line)
        if opener is None:
            if fence:
                opener = fence.group(1)
        elif (fence and fence.group(1)[0] == opener[0]
              and len(fence.group(1)) >= len(opener) and not fence.group(2).strip()):
            opener = None
    return opener is not None


def split_record(text: str) -> dict:
    """A record's text in the parts `parse` reads it by, for the writer
    (task 158) to rewrite one part and keep the rest: the title, the
    header's field lines in order, the body's lines and the history
    section's non-blank lines exactly as written."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title, fields, at = _header(lines)
    body_lines = lines[at:]
    kept, _records, _unread = _history(body_lines)
    history = [line.rstrip() for line in body_lines[len(kept) + 1:] if line.strip()] \
        if len(kept) < len(body_lines) else []
    return {"title": title, "fields": fields, "body": "\n".join(kept).strip("\n"),
            "history": history}


def read_regular(path: Path) -> bytes:
    """A regular file's bytes, never through a link and never over the
    cap — the one way an entry is read, for the writer as for the index."""
    return _read_regular(path)


def parse(name: str, data: bytes) -> dict:
    """One file, read as a version 1 record — or said not to be one.

    `state` is `entry` (valid; `warnings` may say what is incomplete),
    `invalid` (it claims the contract and breaks it; `problems` say how),
    `unsupported` (another contract version) or `unversioned` (no
    `**Brain:**` line, so not an entry). The body and the metadata are
    read from these same bytes, whose digest is `revision`."""
    rec: dict = {"file": name, "revision": revision(data), "bytes": len(data),
                 "problems": [], "warnings": []}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        rec.update(state="invalid", problems=["the file is not UTF-8 text"])
        return rec
    if text.startswith("﻿"):
        text = text[1:]  # a byte-order mark is not part of the title
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title, fields, at = _header(lines)
    seen: dict[str, str] = {}
    for key, value in fields:
        if key in seen:
            rec["problems"].append(f"the header names **{key}:** twice")
        seen[key] = value
    version = seen.get("Brain")
    if version is None:
        rec["state"] = "unversioned"
        rec["problems"].append("there is no **Brain:** line, so this file is "
                               "not a Brain entry")
        return rec
    rec["contract"] = _clean(version, 20)
    raw_id = seen.get("Id", "").strip()
    rec["id"] = raw_id if ID_RE.match(raw_id) else None
    if version.strip() != str(CONTRACT):
        rec["state"] = "unsupported"
        rec["problems"].append(
            f"it declares Brain contract {rec['contract']!r}; this board reads "
            f"version {CONTRACT} only and does not guess at another")
        return rec
    for key in REQUIRED:
        if not seen.get(key, "").strip():
            rec["problems"].append(f"the required **{key}:** line is missing or empty")
    if title is None:
        rec["problems"].append("the first line is not a `# Title`")
    if raw_id and not rec["id"]:
        rec["problems"].append(
            f"**Id:** {_clean(raw_id, 70)!r} is not an id: lower-case letters, "
            "digits and '-', 2 to 64 characters, starting with a letter or digit")
    kind = seen.get("Kind", "").strip()
    if kind and kind not in KINDS:
        rec["problems"].append(f"**Kind:** {_clean(kind, 40)!r} is not one of "
                               + ", ".join(KINDS))
    klass = seen.get("Classification", "").strip()
    if klass and klass not in CLASSIFICATIONS:
        rec["problems"].append(f"**Classification:** {_clean(klass, 40)!r} is not "
                               "one of " + ", ".join(CLASSIFICATIONS))
    for key in seen:
        if key not in FIELDS:
            rec["warnings"].append(f"**{key}:** is not a version {CONTRACT} field; "
                                   "it is ignored")
    body_lines, history, unread = _history(lines[at:])
    for line in unread[:5]:
        rec["warnings"].append(f"a classification history line could not be read: {line}")
    links = {}
    for key, out in (("Related", "related"), ("Supersedes", "supersedes"),
                     ("Superseded by", "supersededBy"), ("Replaced by", "replacedBy")):
        ids = []
        for item in _split_list(seen.get(key, "")):
            if ID_RE.match(item):
                ids.append(item)
            else:
                rec["warnings"].append(f"**{key}:** {_clean(item, 70)!r} is not an "
                                       "entry id; it is ignored")
        links[out] = ids
    rec.update(
        state="invalid" if rec["problems"] else "entry",
        title=_clean(title or "", MAX_TITLE_CHARS) or None,
        kind=kind if kind in KINDS else None,
        classification=klass if klass in CLASSIFICATIONS else None,
        summary=_clean(seen.get("Summary", "")) or None,
        sources=[_clean(s, 200) for s in _split_list(seen.get("Source", ""))],
        related=links["related"], supersedes=links["supersedes"],
        supersededBy=links["supersededBy"][:1], replacedBy=links["replacedBy"][:1],
        removed=_clean(seen["Removed"]) if "Removed" in seen else None,
        invalidated=(_clean(seen["Invalidated"]) or None) if "Invalidated" in seen else None,
        override=(_clean(seen["Override"]) or None) if "Override" in seen else None,
        history=history,
        body="\n".join(body_lines).strip("\n"),
    )
    if rec["state"] == "entry":
        if not rec["summary"]:
            rec["warnings"].append("there is no **Summary:** line")
        if history and history[-1]["classification"] != rec["classification"]:
            rec["warnings"].append(
                f"the newest classification history line says "
                f"{history[-1]['classification']}, and the header says "
                f"{rec['classification']}; the header is what is shown")
        if (rec["classification"] == "Superseded" and not rec["supersededBy"]
                and not rec["invalidated"]):
            rec["warnings"].append("it is Superseded and names no **Superseded by:** "
                                   "entry and no **Invalidated:** decision")
        if rec["removed"] is not None and not rec["removed"]:
            rec["removed"] = "removed — no reason was recorded"
    return rec


# ── the directory ──────────────────────────────────────────────────────


def _read_regular(path: Path) -> bytes:
    """A regular file's bytes, never through a link and never more than
    the cap — refused with a reason otherwise."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("not a regular file")
        if info.st_size > MAX_ENTRY_BYTES:
            raise OSError(f"{info.st_size} bytes, over the {MAX_ENTRY_BYTES}-byte cap")
        chunks, left = [], MAX_ENTRY_BYTES + 1
        while left > 0:
            chunk = os.read(fd, min(65536, left))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_ENTRY_BYTES:
            raise OSError(f"over the {MAX_ENTRY_BYTES}-byte cap")
        return data
    finally:
        os.close(fd)


def _directory_state() -> tuple[str, str | None]:
    """Whether `brain/` can be read: `present`, `missing` or `refused`."""
    path = directory()
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing", None
    except OSError as exc:
        return "refused", f"brain/ could not be examined ({exc.strerror or exc})"
    if stat.S_ISLNK(info.st_mode):
        return "refused", ("brain/ is a link; a link is never followed, because "
                           "it could carry a read out of the work root")
    if not stat.S_ISDIR(info.st_mode):
        return "refused", "brain/ is not a directory"
    if config.escapes(path, config.work_root()):
        return "refused", "brain/ resolves outside the work root"
    return "present", None


def _scan() -> dict:
    """Every record under brain/, read once: the directory's state, the
    parsed files and the files refused before parsing."""
    state, why = _directory_state()
    out = {"state": state, "why": why, "records": [], "refused": [],
           "skipped": 0, "truncated": False}
    if state != "present":
        return out
    root = directory()
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        out.update(state="refused", why=f"brain/ could not be listed ({exc.strerror or exc})")
        return out
    for name in names:
        if name.startswith("."):
            continue
        if not name.endswith(".md"):
            out["skipped"] += 1
            continue
        if len(out["records"]) + len(out["refused"]) >= MAX_FILES:
            out["truncated"] = True
            break
        if not NAME_RE.match(name):
            out["refused"].append({"file": _clean(name, 130),
                                   "why": "the file's name is not a plain flat name"})
            continue
        path = root / name
        try:
            if stat.S_ISLNK(os.lstat(path).st_mode):
                out["refused"].append({"file": name, "why": "it is a link, and a "
                                       "link is never followed"})
                continue
            data = _read_regular(path)
        except OSError as exc:
            out["refused"].append({"file": name, "why": f"it could not be read: "
                                   f"{exc.strerror or exc}"})
            continue
        out["records"].append(parse(name, data))
    return out


def _cards_here() -> set[str]:
    """The card numbers on this board or in its archive, from the stage
    directories' file names alone."""
    numbers = set()
    places = [config.TASKS / slug for slug, _ in config.STAGES]
    places.append(config.TASKS / "archive")
    for place in places:
        try:
            names = os.listdir(place)
        except OSError:
            continue
        for name in names:
            match = taskfiles.NUMBER_RE.match(name)
            if match and name.endswith(".md"):
                numbers.add(taskfiles.canonical_number(match.group(1)))
    return numbers


def _source(item: str, cards: set[str]) -> dict:
    """One `**Source:**` item, said honestly: a card on this board or not,
    a plans/ or reference/ file present or not, a web address (not
    fetched), or text bench does not interpret."""
    card = CARD_REF_RE.match(item)
    if card:
        number = taskfiles.canonical_number(card.group(1))
        return {"kind": "card", "text": item, "card": number,
                "present": number in cards, "address": f"/cards/{number}"}
    found = FILE_REF_RE.match(item)
    if found:
        path = config.work_root() / found.group(1) / found.group(2)
        try:
            present = stat.S_ISREG(os.lstat(path).st_mode)
        except OSError:
            present = False
        return {"kind": "file", "text": item, "present": present,
                "address": f"/files/{found.group(1)}/{found.group(2)}"}
    if URL_RE.match(item):
        return {"kind": "web", "text": item, "present": None, "address": item}
    return {"kind": "text", "text": item, "present": None, "address": None}


def _diagnostics(scan: dict, duplicates: dict[str, list[str]]) -> list[dict]:
    out = [{"file": r["file"], "code": "refused", "message": r["why"]}
           for r in scan["refused"]]
    for rec in scan["records"]:
        if rec["state"] in ("invalid", "unsupported", "unversioned"):
            out.append({"file": rec["file"], "code": rec["state"],
                        "message": "; ".join(rec["problems"][:4])})
    for entry_id, files in sorted(duplicates.items()):
        out.append({"file": ", ".join(files), "code": "duplicate-id",
                    "message": f"{len(files)} files carry the id {entry_id}; none "
                               "of them is shown under it until one is changed"})
    return out


def _resolve_links(entries: list[dict], ids: set[str]) -> None:
    for e in entries:
        for key in ("related", "supersedes", "supersededBy", "replacedBy"):
            for target in e[key]:
                if target not in ids:
                    e["warnings"].append(f"it names {target}, which is not an entry "
                                         "on this board")
        # a supersession chain that comes back on itself has no current end
        seen, at = {e["id"]}, e
        by_id = {x["id"]: x for x in entries}
        while at["supersededBy"]:
            nxt = at["supersededBy"][0]
            if nxt in seen:
                e["warnings"].append("its **Superseded by:** chain loops back on itself")
                break
            seen.add(nxt)
            at = by_id.get(nxt)
            if at is None:
                break


def _read() -> dict:
    scan = _scan()
    valid = [r for r in scan["records"] if r["state"] == "entry"]
    by_id: dict[str, list[dict]] = {}
    for rec in valid:
        by_id.setdefault(rec["id"], []).append(rec)
    duplicates = {i: [r["file"] for r in recs] for i, recs in by_id.items()
                  if len(recs) > 1}
    for rec in valid:
        if rec["id"] in duplicates:
            rec["state"] = "duplicate"
    entries = [r for r in valid if r["state"] == "entry"]
    _resolve_links(entries, {e["id"] for e in entries})
    cards = _cards_here() if entries else set()
    for e in entries:
        e["sourceRefs"] = [_source(s, cards) for s in e["sources"]]
    digest = hashlib.sha256()
    for rec in sorted(scan["records"], key=lambda r: r["file"]):
        digest.update(f"{rec['file']}\0{rec['revision']}\n".encode())
    for ref in scan["refused"]:
        digest.update(f"{ref['file']}\0refused\n".encode())
    return {"scan": scan, "entries": entries, "duplicates": duplicates,
            "revision": digest.hexdigest()[:16] if scan["state"] == "present" else "none"}


def reading() -> dict:
    """Every record under brain/, read once — the same reading `index` and
    `entry` are made from, for the writer (`brainwrite`, task 158) to hold
    a write to: `scan` (the directory's state, every parsed file, the
    refused ones), the valid `entries`, the `duplicates` and `revision`."""
    return _read()


def _listed(e: dict) -> dict:
    return {"id": e["id"], "title": e["title"], "kind": e["kind"],
            "classification": e["classification"], "summary": e["summary"],
            "tombstone": e["removed"] is not None, "revision": e["revision"],
            "file": e["file"], "sources": e["sourceRefs"],
            "supersededBy": e["supersededBy"], "override": e["override"] is not None,
            "warnings": len(e["warnings"])}


def index() -> dict:
    """The Brain as the page lists it. A read of `brain/` and nothing else:
    every valid entry, sorted by title, the counts per
    classification, the directory's state, the bounded diagnostics, and
    `revision`, a digest of every file read — the guard a write (#158)
    will be held to."""
    read = _read()
    scan = read["scan"]
    entries = sorted(read["entries"], key=lambda e: ((e["title"] or "").lower(), e["id"]))
    counts = {c: 0 for c in CLASSIFICATIONS}
    for e in entries:
        counts[e["classification"]] += 1
    diagnostics = _diagnostics(scan, read["duplicates"])
    return {
        "kind": "bench-brain-index", "v": CONTRACT,
        "directory": {"state": scan["state"], "why": scan["why"],
                      "path": f"{DIRECTORY}/"},
        "revision": read["revision"],
        "entries": [_listed(e) for e in entries],
        "counts": counts,
        "kinds": list(KINDS), "classifications": list(CLASSIFICATIONS),
        "diagnostics": diagnostics[:MAX_DIAGNOSTICS],
        "diagnosticsOmitted": max(0, len(diagnostics) - MAX_DIAGNOSTICS),
        "skipped": scan["skipped"], "truncated": scan["truncated"],
        "limits": {"files": MAX_FILES, "entryBytes": MAX_ENTRY_BYTES},
    }


def entry(entry_id: str) -> dict:
    """One entry by its id, from one reading of its file: the metadata and
    the body come from the same bytes, named by `revision`.

    `state` is `entry`, `tombstone` (kept, and said to be removed — with
    what replaced it where that is recorded), `unknown` (nothing on this
    board carries the id), `duplicate` (more than one file does), `invalid`
    or `unsupported` (a file claims the id and cannot be shown as an
    entry). Raises `NotAnId` for a string that cannot be an id, before
    anything is read."""
    if not isinstance(entry_id, str) or not ID_RE.match(entry_id):
        raise NotAnId(f"{str(entry_id)[:70]!r} is not a Brain entry id")
    read = _read()
    scan = read["scan"]
    base = {"kind": "bench-brain-entry", "v": CONTRACT, "id": entry_id,
            "directory": {"state": scan["state"], "why": scan["why"]},
            "brainRevision": read["revision"]}
    if entry_id in read["duplicates"]:
        return {**base, "state": "duplicate", "files": read["duplicates"][entry_id]}
    found = next((e for e in read["entries"] if e["id"] == entry_id), None)
    if found is None:
        claims = [r for r in scan["records"]
                  if r.get("id") == entry_id and r["state"] in ("invalid", "unsupported")]
        if claims:
            rec = claims[0]
            return {**base, "state": rec["state"], "file": rec["file"],
                    "problems": rec["problems"], "revision": rec["revision"]}
        return {**base, "state": "unknown"}
    by_id = {e["id"]: e for e in read["entries"]}

    def name(target: str) -> dict:
        other = by_id.get(target)
        return {"id": target, "title": other["title"] if other else None,
                "present": other is not None}

    linked_from = sorted(
        ({"id": e["id"], "title": e["title"]} for e in read["entries"]
         if e["id"] != entry_id and entry_id in (e["related"] + e["supersedes"]
                                                 + e["supersededBy"] + e["replacedBy"])),
        key=lambda x: x["id"])
    return {
        **base,
        "state": "tombstone" if found["removed"] is not None else "entry",
        "file": found["file"], "revision": found["revision"],
        "title": found["title"], "entryKind": found["kind"],
        "classification": found["classification"], "summary": found["summary"],
        "removed": found["removed"],
        "invalidated": found["invalidated"], "override": found["override"],
        "sources": found["sourceRefs"],
        "related": [name(t) for t in found["related"]],
        "supersedes": [name(t) for t in found["supersedes"]],
        "supersededBy": [name(t) for t in found["supersededBy"]],
        "replacedBy": [name(t) for t in found["replacedBy"]],
        "linkedFrom": linked_from,
        "history": found["history"],
        "warnings": found["warnings"][:MAX_DIAGNOSTICS],
        "body": found["body"],
    }
