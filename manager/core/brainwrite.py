"""The Brain, written by a person (task 158): create and edit a maintained
entry, classify or correct it with an attributed reason, set or clear a
manual override, and retire it as a tombstone — each one a protected,
revision-bound write of one file under `<work root>/brain/`, committed
through the work state's own scoped commit and published by sync.

`brain` reads the record contract; this module is its only writer. What
every write here is held to, and what it promises:

- **One file, inside brain/.** A write names an entry by its id, resolves
  it through `brain.reading()` and replaces that one file atomically. It
  refuses a `brain/` that is a link or not a directory, a file that is a
  link or not regular, a name that is not flat, and anything a link would
  carry out of the work root. It writes no card, no application file, no
  configuration, no gate and no AGENTS file. Creating the first entry is
  the one write that makes `brain/`; reading never does.
- **Exact revisions.** An edit, a classification, an override and a
  retirement each name the revision the page read (`expected`, the digest
  of the file's bytes). Anything else on disk — another person's save, a
  hand edit, a sync that brought a teammate's change — is a conflict: the
  answer carries the current entry beside the one attempted, and nothing
  is written. Nothing here retries a stale write.
- **One operation per request identity.** Every write carries `request`.
  Its intent (the id allocated, the bytes about to be written, the
  revision it replaces) is journalled durably before the file is touched,
  so the same request again — a double click, a lost reply, a restart
  mid-write — answers with, or finishes, the first operation instead of
  writing a second entry or a second history line. The journal is
  machine-local, under `manager/local/state/brain/`.
- **Saved, committed and shared are three facts.** The bytes are saved
  first; under `BOARD_COMMIT_MOVES` they are committed in the work-state
  repository with a pathspec commit that touches nothing else; under
  `BOARD_SYNC` sync publishes the commit. `operation()` reads each state
  again when asked, from git, so a failed commit is never shown as a
  rollback and an unpushed commit never as shared.
- **Attribution and history.** The actor is this board's git identity
  (the operating-system user where git has none), never a field in the
  body. Every classification change, override and retirement appends one
  line to the entry's `## Classification history`, so the history is
  versioned with the file.
- **Supersession.** Superseded names a replacing entry that exists, is
  not retired, is not this entry and does not lead back to it — or an
  explicit invalidation decision with its reason.

A write launches nothing, runs no check and fetches nothing. Reading an
operation's status asks local git only; `retry` with `push` is a person
asking sync to publish now.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import acceptance
import brain
import config
import gitrepo
import records
import state
import sync
import taskfiles

MAX_BODY_BYTES = 200 * 1024
MAX_REASON = 500
MIN_REASON = 3
MAX_SOURCE = 200
JOURNAL_CAP = 300
REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")
REVISION_RE = re.compile(r"^[0-9a-f]{16}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩‎‏]")
_BODY_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")
_URL_USERINFO = re.compile(r"^https?://[^/?#]*@")

_LOCK = threading.Lock()
LOCK = records.InterprocessLock(lambda: config.STATE / "brain" / "write.lock")


class Refused(Exception):
    """A write bench will not make, with the status the API answers and
    any extra fields (`stale`, `current`, `field`)."""

    def __init__(self, message: str, status: int = 400, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


# ── where things live ──────────────────────────────────────────────────


def journal_path() -> Path:
    return config.STATE / "brain" / "operations.json"


def _journal() -> dict:
    data = records.read_json(journal_path(), {}, expect=dict)
    return data if isinstance(data, dict) else {}


def _journal_put(request: str, entry: dict) -> None:
    data = _journal()
    data[request] = entry
    if len(data) > JOURNAL_CAP:
        done = sorted((k for k in data if data[k].get("status") == "done"),
                      key=lambda k: data[k].get("at", ""))
        for key in done[:len(data) - JOURNAL_CAP]:
            del data[key]
    records.write_json(journal_path(), data, durable=True)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(value) -> str:
    return hashlib.sha256(records.dumps(value, indent=None).encode()).hexdigest()[:16]


# ── what a person may write ────────────────────────────────────────────


def _line(value, what: str, *, cap: int, required: bool = False,
          minimum: int = 1) -> str:
    """One line of text a person typed: no line breaks, no control or
    bidirectional characters, no markup that could pose as something
    else. Refused, never quietly changed, except for runs of spaces."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Refused(f"{what} must be text", field=what)
    if "\n" in value or "\r" in value:
        raise Refused(f"{what} is one line; line breaks are not allowed", field=what)
    if _CONTROL.search(value):
        raise Refused(f"{what} contains control or direction-changing characters",
                      field=what)
    text = " ".join(value.split())
    if "<" in text or ">" in text:
        raise Refused(f"{what} cannot contain '<' or '>': it is shown as plain text and "
                      "never as markup", field=what)
    if required and len(text) < minimum:
        raise Refused(f"{what} is required" + (f" (at least {minimum} characters)"
                                               if minimum > 1 else ""), field=what)
    if len(text) > cap:
        raise Refused(f"{what} is at most {cap} characters", field=what)
    return text


def _reason(value, what: str = "reason") -> str:
    return _line(value, what, cap=MAX_REASON, required=True, minimum=MIN_REASON)


def _title(value) -> str:
    text = _line(value, "title", cap=brain.MAX_TITLE_CHARS, required=True)
    if text.startswith("#"):
        raise Refused("the title is plain text; it cannot start with '#'", field="title")
    if text.endswith("#"):
        # Markdown reads a heading's trailing '#'s as its closing sequence,
        # so the title saved would not be the title read back
        raise Refused("the title cannot end with '#': it would be read back without it",
                      field="title")
    return text


def _ids(value, what: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",")]
    if not isinstance(value, list):
        raise Refused(f"{what} is a list of entry ids", field=what)
    out: list[str] = []
    for item in value:
        item = item.strip() if isinstance(item, str) else item
        if not item:
            continue
        if not isinstance(item, str) or not brain.ID_RE.match(item):
            raise Refused(f"{what}: {str(item)[:70]!r} is not an entry id", field=what)
        if item not in out:
            out.append(item)
    if len(out) > brain.MAX_LIST_ITEMS:
        raise Refused(f"{what} names at most {brain.MAX_LIST_ITEMS} entries", field=what)
    return out


def _sources(value) -> list[str]:
    """Where an entry comes from: a card (`#148`), a `plans/` or
    `reference/` file, a web address (shown, never fetched) or plain text.
    One item per line or list element; a comma would split an item in the
    file, so it is refused rather than guessed at."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = value.split("\n")
    if not isinstance(value, list):
        raise Refused("sources are a list", field="sources")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise Refused("each source is text", field="sources")
        text = _line(item, "a source", cap=MAX_SOURCE)
        if not text:
            continue
        if "," in text:
            raise Refused(f"source {text[:60]!r} contains a comma; put each source on its "
                          "own line", field="sources")
        if _URL_USERINFO.match(text):
            raise Refused("a web source cannot carry a user name or password",
                          field="sources")
        if text.lower().startswith(("javascript:", "data:", "file:", "vbscript:")):
            raise Refused(f"source {text[:60]!r} is not a link bench shows", field="sources")
        if text not in out:
            out.append(text)
    if len(out) > brain.MAX_LIST_ITEMS:
        raise Refused(f"at most {brain.MAX_LIST_ITEMS} sources", field="sources")
    return out


def _body(value) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Refused("the body is Markdown text", field="body")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if _BODY_CONTROL.search(text):
        raise Refused("the body contains control or direction-changing characters",
                      field="body")
    if len(text.encode("utf-8")) > MAX_BODY_BYTES:
        raise Refused(f"the body is at most {MAX_BODY_BYTES // 1024} KiB", field="body")
    lines = text.split("\n")
    fenced = brain.fence_marks(lines)
    for n, line in enumerate(lines):
        if not fenced[n] and line.strip() == brain.HISTORY_HEADING:
            raise Refused("the body cannot contain a '## Classification history' heading: "
                          "that section is written by the board", field="body")
    if brain.fence_unclosed(lines):
        # an unclosed fence would swallow the history section after it
        raise Refused("a code fence in the body is never closed; close it with a line of "
                      "the same backticks or tildes", field="body")
    return text.strip("\n")


def _kind(value) -> str:
    if value not in brain.KINDS:
        raise Refused("kind is one of " + ", ".join(brain.KINDS), field="kind")
    return value


def _classification(value) -> str:
    if value not in brain.CLASSIFICATIONS:
        raise Refused("classification is one of " + ", ".join(brain.CLASSIFICATIONS),
                      field="classification")
    return value


def _who() -> str:
    found = acceptance.actor()
    name = " ".join(re.sub(r"[·—<>]", " ", _CONTROL.sub("", found.get("name") or "")).split())
    if not name:
        raise Refused("a Brain write names who made it, and this board has no identity — "
                      "set `git config user.name` in the work-state repository. Nothing "
                      "was written.", 409)
    return name[:80]


# ── the file ───────────────────────────────────────────────────────────

_ORDER = ("Brain", "Id", "Kind", "Classification", "Summary", "Source", "Related",
          "Supersedes", "Superseded by", "Invalidated", "Override", "Removed",
          "Replaced by")


def render(title: str, fields: dict, body: str, history: list[str],
           extra: list[tuple[str, str]] = ()) -> str:
    """A version 1 record from its parts: the title, the header fields in
    a fixed order (empty ones left out), any fields this version does not
    read carried over after them, the body, and the history section."""
    lines = [f"# {title}", ""]
    for key in _ORDER:
        value = fields.get(key)
        if value not in (None, ""):
            lines.append(f"**{key}:** {value}")
    for key, value in extra:
        lines.append(f"**{key}:** {value}")
    lines.append("")
    if body:
        lines += [body, ""]
    if history:
        lines += [brain.HISTORY_HEADING, ""] + history
    return "\n".join(lines).rstrip("\n") + "\n"


def _history_line(at: str, klass: str, actor: str, reason: str) -> str:
    return f"- {at} · {klass} · {actor} — {reason}"


def _parts(data: bytes) -> dict:
    """An existing record split for rewriting: the parsed record, its
    header fields as written, the ones this version does not read, and
    the history section's lines exactly as they are."""
    rec = brain.parse("x.md", data)
    split = brain.split_record(data.decode("utf-8"))
    fields = dict(split["fields"])
    extra = [(k, v) for k, v in split["fields"] if k not in brain.FIELDS]
    return {"rec": rec, "fields": fields, "extra": extra,
            "title": split["title"] or rec.get("title") or "", "body": split["body"],
            "history": split["history"]}


def _appendable(parts: dict, found: dict) -> dict:
    """An existing record a classify, an override or a retirement may add a
    history line to. A body whose code fence never closes (written by
    hand: an edit through the board refuses one) would swallow the history
    section written after it, so the write is refused instead, with the
    remedy."""
    if brain.fence_unclosed(parts["body"].split("\n")):
        raise Refused(f"brain/{found['file']} has a code fence in its body that is never "
                      "closed, so a history line written after it would read as body. Close "
                      "the fence (Edit, or by hand), then try again", 409)
    return parts


def _check_directory(create: bool, make: bool = True) -> Path:
    """`brain/`, refused when a write must not go through it. A missing
    one is made only by a create that is about to write (`make`): the one
    explicit, authorized write that provisions it."""
    root = config.work_root()
    path = brain.directory()
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if not create:
            raise Refused("there is no brain/ directory, so there is no entry to change", 404)
        if not root.is_dir():
            raise Refused("the work root does not exist; nothing was written", 409)
        if make:
            os.mkdir(path)
        return path
    if stat.S_ISLNK(info.st_mode):
        raise Refused("brain/ is a link; bench never writes through one, because it could "
                      "carry the write out of the work root", 409)
    if not stat.S_ISDIR(info.st_mode):
        raise Refused("brain/ is not a directory; nothing was written", 409)
    if config.escapes_work(path) is not None:
        raise Refused("brain/ resolves outside the work root; nothing was written", 409)
    return path


def _current_bytes(path: Path) -> bytes | None:
    """The file's bytes now, None when it is not there; refused when it is
    something a write must not replace."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        raise Refused(f"brain/{path.name} is a link; bench never writes through one", 409)
    if not stat.S_ISREG(info.st_mode):
        raise Refused(f"brain/{path.name} is not a regular file; nothing was written", 409)
    if config.escapes_work(path) is not None:
        raise Refused(f"brain/{path.name} resolves outside the work root", 409)
    try:
        return brain.read_regular(path)
    except OSError as exc:
        raise Refused(f"brain/{path.name} could not be read: {exc}", 409) from exc


# ── resolving what a write is about ────────────────────────────────────


def _found(reading: dict, entry_id: str) -> dict:
    if not isinstance(entry_id, str) or not brain.ID_RE.match(entry_id):
        raise Refused(f"{str(entry_id)[:70]!r} is not a Brain entry id", field="id")
    if entry_id in reading["duplicates"]:
        raise Refused(f"{entry_id} is claimed by "
                      f"{', '.join(reading['duplicates'][entry_id])}; change one of them "
                      "by hand first — the board does not pick one", 409)
    found = next((e for e in reading["entries"] if e["id"] == entry_id), None)
    if found is None:
        claims = [r for r in reading["scan"]["records"] if r.get("id") == entry_id]
        if claims:
            raise Refused(f"brain/{claims[0]['file']} carries {entry_id} and does not follow "
                          "the entry format; fix it by hand before writing it here", 409)
        raise Refused(f"no entry {entry_id} on this board", 404)
    return found


def _targets(reading: dict, ids: list[str], what: str, *, self_id: str | None,
             allow_tombstone: bool = True) -> None:
    by_id = {e["id"]: e for e in reading["entries"]}
    for target in ids:
        if target == self_id:
            raise Refused(f"{what} cannot name the entry itself", field=what)
        other = by_id.get(target)
        if other is None:
            raise Refused(f"{what}: {target} is not an entry on this board — create it "
                          "first or remove it from the list", field=what)
        if not allow_tombstone and other["removed"] is not None:
            raise Refused(f"{what}: {target} is retired; name the entry that replaced it",
                          field=what)


def _no_cycle(reading: dict, entry_id: str, target: str) -> None:
    """Following `target`'s own **Superseded by:** chain must never come
    back to `entry_id`: a supersession loop has no current end."""
    by_id = {e["id"]: e for e in reading["entries"]}
    seen, at = {target}, by_id.get(target)
    while at is not None and at["supersededBy"]:
        nxt = at["supersededBy"][0]
        if nxt == entry_id:
            raise Refused(f"{target} is itself superseded, through {', '.join(sorted(seen))}, "
                          f"by {entry_id}: marking {entry_id} superseded by it would make a "
                          "loop. Name the entry that is current now.", field="supersededBy")
        if nxt in seen:
            break
        seen.add(nxt)
        at = by_id.get(nxt)


def _guard_revision(found: dict, payload: dict, reading: dict) -> None:
    expected = payload.get("expected")
    if expected != found["revision"]:
        raise Refused(
            f"{found['id']} changed since this page read it (it read "
            f"{str(expected)[:16] or 'nothing'}, the file is now {found['revision']}). "
            "Nothing was written. Read the current version, bring your change across, "
            "and save again.", 409, stale=True, current=brain.entry(found["id"]))


# ── the plans: what each write would put on disk ──────────────────────


def _allocate(reading: dict, title: str) -> str:
    taken = {r.get("id") for r in reading["scan"]["records"]}
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "entry"
    for _ in range(50):
        candidate = f"e-{slug}-{secrets.token_hex(3)}"
        if candidate not in taken and brain.ID_RE.match(candidate) and \
                not (brain.directory() / f"{candidate}.md").exists():
            return candidate
    raise Refused("no free entry id could be allocated; nothing was written", 500)


def _plan_create(payload: dict, reading: dict, actor: str) -> dict:
    title = _title(payload.get("title"))
    kind = _kind(payload.get("kind"))
    summary = _line(payload.get("summary"), "summary", cap=brain.MAX_FIELD_CHARS)
    sources = _sources(payload.get("sources"))
    related = _ids(payload.get("related"), "related")
    _targets(reading, related, "related", self_id=None)
    body = _body(payload.get("body"))
    klass = _classification(payload.get("classification") or "Unclassified")
    if klass == "Superseded":
        raise Refused("a new entry cannot start Superseded; create it, then classify it "
                      "against the entry that replaces it", field="classification")
    reason = (_reason(payload.get("reason")) if klass != "Unclassified" or payload.get("reason")
              else "written; not yet assessed")
    entry_id = _allocate(reading, title)
    fields = {"Brain": str(brain.CONTRACT), "Id": entry_id, "Kind": kind,
              "Classification": klass, "Summary": summary,
              "Source": ", ".join(sources), "Related": ", ".join(related)}
    text = render(title, fields, body, [_history_line(_now(), klass, actor, reason)])
    return {"id": entry_id, "file": f"{entry_id}.md", "expected": None, "text": text,
            "what": "created"}


def _plan_edit(payload: dict, reading: dict, actor: str) -> dict:
    found = _found(reading, payload.get("id"))
    if found["removed"] is not None:
        raise Refused(f"{found['id']} is retired; a tombstone is kept as it is", 409)
    _guard_revision(found, payload, reading)
    parts = _parts(_current_bytes(brain.directory() / found["file"]) or b"")
    title = _title(payload.get("title"))
    kind = _kind(payload.get("kind"))
    summary = _line(payload.get("summary"), "summary", cap=brain.MAX_FIELD_CHARS)
    sources = _sources(payload.get("sources"))
    related = _ids(payload.get("related"), "related")
    _targets(reading, related, "related", self_id=found["id"])
    body = _body(payload.get("body"))
    fields = dict(parts["fields"])
    fields.update({"Kind": kind, "Summary": summary, "Source": ", ".join(sources),
                   "Related": ", ".join(related)})
    text = render(title, fields, body, parts["history"], parts["extra"])
    return {"id": found["id"], "file": found["file"], "expected": found["revision"],
            "text": text, "what": "edited"}


def _plan_classify(payload: dict, reading: dict, actor: str) -> dict:
    found = _found(reading, payload.get("id"))
    if found["removed"] is not None:
        raise Refused(f"{found['id']} is retired; a tombstone is kept as it is", 409)
    _guard_revision(found, payload, reading)
    klass = _classification(payload.get("classification"))
    reason = _reason(payload.get("reason"))
    parts = _appendable(_parts(_current_bytes(brain.directory() / found["file"]) or b""), found)
    fields = dict(parts["fields"])
    fields["Classification"] = klass
    fields.pop("Superseded by", None)
    fields.pop("Invalidated", None)
    if klass == "Superseded":
        target = (payload.get("supersededBy") or "").strip() if isinstance(
            payload.get("supersededBy"), str) else payload.get("supersededBy")
        invalidation = payload.get("invalidation")
        if target and invalidation:
            raise Refused("Superseded names either the replacing entry or an invalidation "
                          "decision, not both", field="supersededBy")
        if target:
            _ids([target], "supersededBy")
            _targets(reading, [target], "supersededBy", self_id=found["id"],
                     allow_tombstone=False)
            _no_cycle(reading, found["id"], target)
            fields["Superseded by"] = target
            reason = f"superseded by {target}: {reason}"
        elif invalidation:
            fields["Invalidated"] = _reason(invalidation, "invalidation")
            reason = f"invalidated without a replacement: {reason}"
        else:
            raise Refused("Superseded needs the entry that replaces this one, or an explicit "
                          "invalidation decision with its reason", field="supersededBy")
    history = parts["history"] + [_history_line(_now(), klass, actor, reason)]
    text = render(parts["title"], fields, parts["body"], history, parts["extra"])
    return {"id": found["id"], "file": found["file"], "expected": found["revision"],
            "text": text, "what": f"classified {klass}"}


def _plan_override(payload: dict, reading: dict, actor: str) -> dict:
    found = _found(reading, payload.get("id"))
    if found["removed"] is not None:
        raise Refused(f"{found['id']} is retired; a tombstone is kept as it is", 409)
    _guard_revision(found, payload, reading)
    action = payload.get("action")
    if action not in ("set", "clear"):
        raise Refused("an override is set or cleared", field="action")
    reason = _reason(payload.get("reason"))
    parts = _appendable(_parts(_current_bytes(brain.directory() / found["file"]) or b""), found)
    fields = dict(parts["fields"])
    at = _now()
    if action == "set":
        fields["Override"] = f"manual · {actor} · {at} — {reason}"
        said = f"manual override set: {reason}"
    else:
        if "Override" not in fields:
            raise Refused(f"{found['id']} has no manual override to clear", 409)
        fields.pop("Override")
        said = f"manual override cleared: {reason}"
    history = parts["history"] + [_history_line(at, found["classification"], actor, said)]
    text = render(parts["title"], fields, parts["body"], history, parts["extra"])
    return {"id": found["id"], "file": found["file"], "expected": found["revision"],
            "text": text, "what": f"override {'set' if action == 'set' else 'cleared'}"}


def _plan_retire(payload: dict, reading: dict, actor: str) -> dict:
    found = _found(reading, payload.get("id"))
    if found["removed"] is not None:
        raise Refused(f"{found['id']} is already retired", 409)
    _guard_revision(found, payload, reading)
    reason = _reason(payload.get("reason"))
    replaced = payload.get("replacedBy") or ""
    parts = _appendable(_parts(_current_bytes(brain.directory() / found["file"]) or b""), found)
    fields = dict(parts["fields"])
    fields["Removed"] = reason
    said = f"retired: {reason}"
    if replaced:
        _ids([replaced], "replacedBy")
        _targets(reading, [replaced], "replacedBy", self_id=found["id"],
                 allow_tombstone=False)
        fields["Replaced by"] = replaced
        said += f" (replaced by {replaced})"
    history = parts["history"] + [_history_line(_now(), found["classification"], actor, said)]
    text = render(parts["title"], fields, parts["body"], history, parts["extra"])
    return {"id": found["id"], "file": found["file"], "expected": found["revision"],
            "text": text, "what": "retired"}


PLANS = {"create": _plan_create, "edit": _plan_edit, "classify": _plan_classify,
         "override": _plan_override, "retire": _plan_retire}


# ── the operation ──────────────────────────────────────────────────────


def _request(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    request = payload.get("request")
    if not isinstance(request, str) or not REQUEST_RE.match(request):
        raise Refused("a Brain write names a request identity (8–80 letters, digits or "
                      "._:-), so a repeat answers rather than writes twice")
    return request


def _write(path: Path, text: str) -> str:
    data = text.encode("utf-8")
    records.write_bytes(path, data, durable=True, mkdir=False)
    return brain.revision(data)


def _commit(path: Path, entry_id: str, what: str, actor: str) -> dict:
    if not config.COMMIT_MOVES:
        return {"state": "off", "detail": "committing board edits is off "
                "(BOARD_COMMIT_MOVES); the file is saved and not committed"}
    if config.state_repo() is None:
        return {"state": "failed", "detail": "the work root is in no work-state repository"}
    failure = f"brain/{path.name}: {what} saved, but committing it failed"
    # the events already listed, held so none of their ids can be reused:
    # the narration this commit adds is told apart by identity, not by the
    # list's length, which stops growing once it reaches its cap
    before = list(state.BOARD_EVENTS)
    seen = {id(e) for e in before}
    ok = taskfiles.commit_work_file(
        path, f"board: brain {entry_id} {what} ({actor})", failure)
    if ok:
        sha = _git("log", "-1", "--format=%H", "--", str(path)).stdout.strip()
        return {"state": "committed", "sha": sha}
    said = [e.get("summary", "") for e in list(state.BOARD_EVENTS)
            if id(e) not in seen and str(e.get("summary", "")).startswith(failure)]
    del before
    detail = said[-1][len(failure):].lstrip(": ") if said else "git refused the commit"
    return {"state": "failed", "detail": detail[:300]}


def _git(*args: str, timeout: float = 20) -> subprocess.CompletedProcess:
    root = gitrepo.state()
    if root is None:
        return subprocess.CompletedProcess(args, 128, "", "no work-state repository")
    return gitrepo.git(root, *args, timeout=timeout)


def write(kind: str, payload: dict) -> dict:
    """One protected write of one Brain entry. Returns the operation's
    public result; raises `Refused` with nothing written."""
    if kind not in PLANS:
        raise Refused(f"unknown Brain write {kind!r}")
    request = _request(payload)
    digest = _digest({k: v for k, v in payload.items() if k != "request"} | {"kind": kind})
    with _LOCK, LOCK:
        prior = _journal().get(request)
        if prior is not None:
            if prior.get("kind") != kind or prior.get("digest") != digest:
                raise Refused("this request identity was already used for a different "
                              "Brain write; nothing was written", 409)
            if prior.get("status") == "done" and not prior.get("saved"):
                # the first answer was a refusal: the replay says so again
                raise Refused(f"this request was refused ({prior.get('refused') or 'not saved'}); "
                              "nothing was written. Read the current version and save again "
                              "as a new request.", 409, stale=True,
                              current=_current_entry(prior.get("id")))
            if prior.get("status") == "done":
                return dict(_public(prior), duplicate=True)
            return _finish(request, prior, resumed=True)
        actor = _who()
        reading = brain.reading()
        _check_directory(create=(kind == "create"), make=False)
        plan = PLANS[kind](payload, reading, actor)
        entry = {"kind": kind, "digest": digest, "status": "started", "at": _now(),
                 "id": plan["id"], "file": plan["file"], "expected": plan["expected"],
                 "target": brain.revision(plan["text"].encode("utf-8")),
                 "text": plan["text"], "what": plan["what"], "actor": actor}
        _journal_put(request, entry)
        return _finish(request, entry, resumed=False)


def _finish(request: str, entry: dict, *, resumed: bool) -> dict:
    """Carry a journalled intent to its end: write the bytes unless they
    are already there, commit, and record the outcome. A file that is
    neither what the intent replaces nor what it writes is someone else's
    change: the intent is closed as refused and nothing is written."""
    directory = _check_directory(create=entry["kind"] == "create")
    path = directory / entry["file"]
    if not brain.NAME_RE.match(entry["file"]):
        raise Refused("the entry's file name is not a plain flat name", 409)
    current = _current_bytes(path)
    now = brain.revision(current) if current is not None else None
    if now == entry["target"]:
        revision = now                      # saved before; a reply was lost
    elif now == entry["expected"]:
        # read once more immediately before the replace: a writer outside
        # this process may have saved since the plan was made
        again = _current_bytes(path)
        if (brain.revision(again) if again is not None else None) != now:
            return _conflict(request, entry)
        revision = _write(path, entry.get("text") or "")
    else:
        return _conflict(request, entry)
    commit = _commit(path, entry["id"], entry["what"], entry["actor"])
    done = {k: v for k, v in entry.items() if k != "text"}
    done.update(status="done", revision=revision, saved=True, commit=commit,
                finished=_now(), resumed=resumed)
    _journal_put(request, done)
    state.record_board_event({
        "kind": "brain", "actor": entry["actor"], "file": f"brain/{entry['file']}",
        "summary": f"Brain: {entry['id']} {entry['what']} by {entry['actor']} — "
                   + ("committed" if commit["state"] == "committed"
                      else "saved, not committed" if commit["state"] == "off"
                      else "saved; the commit failed")})
    state.broadcast({"type": "board"})
    return _public(done)


def _conflict(request: str, entry: dict) -> dict:
    refused = {k: v for k, v in entry.items() if k != "text"}
    refused.update(status="done", saved=False, refused="conflict", finished=_now())
    _journal_put(request, refused)
    raise Refused(f"brain/{entry['file']} changed while this write was being made; "
                  "nothing was written. Read the current version and save again.", 409,
                  stale=True, current=_current_entry(entry["id"]))


def _current_entry(entry_id) -> dict | None:
    try:
        return brain.entry(entry_id)
    except brain.NotAnId:
        return None


# ── what an operation says now ─────────────────────────────────────────


def _push(commit: dict, file: str, revision: str) -> dict:
    if commit.get("state") != "committed":
        return {"state": "not-committed"}
    if not config.SYNC:
        return {"state": "off", "detail": "sync is off (BOARD_SYNC); the commit stays in "
                "this work-state repository until someone pushes it"}
    remote = config.state_git_remote()
    if not remote:
        return {"state": "failed", "detail": "sync has no remote to publish through"}
    upstream = f"{remote}/{config.STATE_BASE}"
    sha = commit.get("sha") or ""
    if sha and _git("merge-base", "--is-ancestor", sha, upstream).returncode == 0:
        return {"state": "shared", "upstream": upstream}
    # sync replays the board's own commits onto what arrived, which gives
    # the commit a new id: the remote holding these exact bytes is shared too
    shown = _git("show", f"{upstream}:{_repo_path(file)}")
    if shown.returncode == 0 and brain.revision(shown.stdout.encode("utf-8")) == revision:
        return {"state": "shared", "upstream": upstream}
    status = sync.status()
    if status.get("state") in ("offline", "stalled"):
        return {"state": "failed", "detail": status.get("detail") or status["state"],
                "upstream": upstream}
    return {"state": "pending", "upstream": upstream}


def _repo_path(file: str) -> str:
    """`brain/<file>` as the work-state repository names it: relative to
    the repository's top level, which is the work root in an external
    layout and an ancestor of it in a vendored one."""
    target = brain.directory() / file
    root = gitrepo.state()
    if root is not None:
        try:
            return Path(os.path.relpath(target.resolve(), Path(root).resolve())).as_posix()
        except ValueError:
            pass
    return f"{brain.DIRECTORY}/{file}"


def _public(entry: dict) -> dict:
    commit = entry.get("commit") or {}
    out = {"kind": entry.get("kind"), "id": entry.get("id"), "file": entry.get("file"),
           "what": entry.get("what"), "actor": entry.get("actor"),
           "at": entry.get("at"), "finished": entry.get("finished"),
           "status": entry.get("status"), "saved": bool(entry.get("saved")),
           "revision": entry.get("revision"), "commit": commit,
           "refused": entry.get("refused")}
    if entry.get("saved"):
        out["push"] = _push(commit, entry.get("file") or "", entry.get("revision") or "")
        # what is on disk now, said beside what this operation saved
        try:
            current = _current_bytes(brain.directory() / (entry.get("file") or ""))
            out["onDisk"] = brain.revision(current) if current is not None else None
        except Refused:
            out["onDisk"] = None
    return out


def operation(request: str) -> dict | None:
    """An operation's state, read now: saved, committed or why not, and
    whether the remote holds it — or None for a request this board has no
    record of."""
    if not isinstance(request, str) or not REQUEST_RE.match(request):
        raise Refused("not a request identity")
    entry = _journal().get(request)
    if entry is None:
        return None
    return dict(_public(entry), request=request)


def retry(payload: dict) -> dict:
    """A person asking again for the half that did not happen: `commit`
    commits the bytes this operation saved, only while the file still holds
    exactly them; `push` asks sync to publish now. Neither writes the
    entry again."""
    request = payload.get("request") if isinstance(payload, dict) else None
    action = payload.get("action") if isinstance(payload, dict) else None
    if not isinstance(request, str) or not REQUEST_RE.match(request):
        raise Refused("name the operation to retry")
    if action not in ("commit", "push"):
        raise Refused("a retry asks for the commit or the push, by name", field="action")
    with _LOCK, LOCK:
        entry = _journal().get(request)
        if entry is None or not entry.get("saved"):
            raise Refused("no saved Brain write has this request identity", 404)
        if action == "commit":
            if (entry.get("commit") or {}).get("state") == "committed":
                return dict(_public(entry), request=request)
            if not config.COMMIT_MOVES:
                raise Refused("committing board edits is off (BOARD_COMMIT_MOVES)", 409)
            path = _check_directory(create=False) / entry["file"]
            current = _current_bytes(path)
            if current is None or brain.revision(current) != entry.get("revision"):
                raise Refused(f"brain/{entry['file']} has changed since this write saved it; "
                              "its newer version is committed by its own save", 409)
            entry["commit"] = _commit(path, entry["id"], entry["what"], entry["actor"])
            _journal_put(request, entry)
            state.broadcast({"type": "board"})
            return dict(_public(entry), request=request)
        if action == "push":
            if (entry.get("commit") or {}).get("state") != "committed":
                raise Refused("there is no commit to publish yet", 409)
            if not config.SYNC:
                raise Refused("sync is off (BOARD_SYNC); push the work-state repository "
                              "yourself", 409)
    outcome = sync.push_now()
    out = operation(request) or {}
    out["pushOutcome"] = outcome
    state.broadcast({"type": "board"})
    return out


def revisions(entry_id: str, limit: int = 30) -> dict:
    """An entry's revisions: the work-state commits that touched its file,
    newest first (a local read of git — nothing is fetched), and this
    board's own saves of it, with their commit state."""
    if not isinstance(entry_id, str) or not brain.ID_RE.match(entry_id):
        raise brain.NotAnId(f"{str(entry_id)[:70]!r} is not a Brain entry id")
    reading = brain.reading()
    found = next((e for e in reading["entries"] if e["id"] == entry_id), None)
    commits: list[dict] = []
    file = found["file"] if found else None
    if file and config.state_repo() is not None:
        path = str(brain.directory() / file)
        log = _git("log", f"-{limit}", "--follow", "--format=%H%x1f%aI%x1f%an%x1f%s", "--",
                   path)
        for line in log.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append({"sha": parts[0], "at": parts[1],
                                "author": _CONTROL.sub("", parts[2])[:80],
                                "subject": _CONTROL.sub("", parts[3])[:200]})
        dirty = _git("status", "--porcelain", "--", path).stdout.strip()
    else:
        dirty = ""
    saves = sorted(
        (dict(_public(e), request=k) for k, e in _journal().items() if e.get("id") == entry_id),
        key=lambda x: x.get("at") or "", reverse=True)[:limit]
    return {"kind": "bench-brain-revisions", "id": entry_id,
            "file": file, "revision": found["revision"] if found else None,
            "uncommitted": bool(dirty), "commits": commits, "saves": saves,
            "history": found["history"] if found else []}


__all__ = ["Refused", "render", "write", "operation", "retry", "revisions",
           "journal_path"]
