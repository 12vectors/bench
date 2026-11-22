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
import brainreviews
import config
import gitrepo
import handbook
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


AUTHORITY_KEYS = ("standing", "policy", "bindings", "assessed")


def _authority(payload: dict, fields: dict) -> None:
    """The source-authority fields a person set on an entry (task 171),
    each written only when the request names it — so a write that does
    not (the Brain Agent's Apply, an older page) keeps what is there.
    Empty clears it. Refused, never guessed at."""
    if "standing" in payload:
        value = payload.get("standing") or ""
        if not isinstance(value, str) or (value and value not in brain.STANDINGS):
            raise Refused("standing is one of " + ", ".join(brain.STANDINGS) +
                          ", or empty for not stated", field="standing")
        fields["Standing"] = value
    if "policy" in payload:
        value = payload.get("policy") or ""
        note = _line(payload.get("policyNote"), "policy note", cap=MAX_SOURCE)
        if not isinstance(value, str) or (value and value not in brain.POLICIES):
            raise Refused("policy is approved or inferred, or empty for not stated",
                          field="policy")
        if note and not value:
            raise Refused("a policy note needs the policy it is about", field="policy")
        fields["Policy"] = f"{value} — {note}" if value and note else value
    if "bindings" in payload:
        value = payload.get("bindings") or []
        if isinstance(value, str):
            value = value.split("\n")
        if not isinstance(value, list):
            raise Refused("bindings are a list", field="bindings")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise Refused("each binding is text", field="bindings")
            text = _line(item, "a binding", cap=MAX_SOURCE)
            if not text:
                continue
            if brain.parse_binding(text) is None:
                raise Refused(f"{text[:80]!r} is not a binding: app:<path>@<commit>, "
                              "work:<path>@<commit>, review:#<card>/<review> or "
                              "deploy:<where>@<identity>", field="bindings")
            if text not in out:
                out.append(text)
        if len(out) > brain.MAX_LIST_ITEMS:
            raise Refused(f"at most {brain.MAX_LIST_ITEMS} bindings", field="bindings")
        fields["Bindings"] = ", ".join(out)
    if "assessed" in payload:
        value = payload.get("assessed") or ""
        if not isinstance(value, str):
            raise Refused("assessed is a date, YYYY-MM-DD", field="assessed")
        if value:
            if not brain.DATE_RE.match(value):
                raise Refused("assessed is a date, YYYY-MM-DD", field="assessed")
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise Refused("assessed is a date, YYYY-MM-DD", field="assessed") from exc
        fields["Assessed"] = value


def check_fields(payload: dict) -> dict:
    """The fields an entry's create or edit would write, normalised by the
    very rules `write` holds them to — or `Refused` naming the field. For
    a caller that must validate a proposed entry before anyone applies it
    (the Brain drafting run's answer, task 175). Reads and writes nothing."""
    if not isinstance(payload, dict):
        raise Refused("the entry is a JSON object")
    out = {"title": _title(payload.get("title")),
           "kind": _kind(payload.get("kind")),
           "summary": _line(payload.get("summary"), "summary", cap=brain.MAX_FIELD_CHARS),
           "sources": _sources(payload.get("sources")),
           "related": _ids(payload.get("related"), "related"),
           "body": _body(payload.get("body"))}
    if payload.get("classification") is not None:
        out["classification"] = _classification(payload.get("classification"))
    return out


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
          "Standing", "Policy", "Bindings", "Assessed", "Supersedes", "Superseded by", "Invalidated", "Override", "Removed",
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
    _authority(payload, fields)
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
    _authority(payload, fields)
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


# ── a filed review's classification (task 168) ─────────────────────────
#
# A review's report stays on its card; what a person records about it in
# the Brain is one small record under brain/reviews/ (the format is
# brainreviews'). These writes name the card and the review, never a file,
# and are refused for anything that would touch the report, the card or a
# maintained entry.

REVIEW_FIELDS = {"request", "card", "review", "expected", "classification", "reason",
                 "supersededBy", "invalidation", "action"}


def _review_target(payload: dict) -> tuple[dict, dict]:
    for key in payload:
        if key not in REVIEW_FIELDS:
            raise Refused(f"{str(key)[:40]!r} cannot be written here: a review's report and "
                          "its card are not edited from the Brain — only its classification "
                          "is recorded, beside it", field=str(key)[:40])
    card_id = payload.get("card")
    if isinstance(card_id, int) and not isinstance(card_id, bool):
        card_id = str(card_id)
    try:
        where = brainreviews.locate(card_id, payload.get("review"))
    except brainreviews.NotAnAddress as exc:
        raise Refused(str(exc), field="review") from exc
    if where["state"] == "ambiguous":
        raise Refused(f"{len(where['files'])} cards hold this review "
                      f"({', '.join(where['files'])}); the board does not pick one — "
                      "give one card a number of its own first", 409)
    if where["state"] != "found":
        raise Refused("that review is not on any card on this board; there is nothing "
                      "to classify", 404)
    card = where["card"]
    if not card["number"]:
        raise Refused(f"{card['file']} has no number, so a classification has no stable "
                      "card to be kept under; give the card a number first", 409)
    return card, where["review"]


def render_review(number: str, review_id: str, fields: dict, history: list[str]) -> str:
    """A version 1 review record from its parts (brainreviews reads it)."""
    lines = [f"# Review classification: #{number} {review_id}", "",
             f"**Brain review:** {brainreviews.CONTRACT}",
             f"**Review of:** #{number}/{review_id}",
             f"**Classification:** {fields['Classification']}"]
    for key in ("Superseded by", "Invalidated", "Override"):
        if fields.get(key):
            lines.append(f"**{key}:** {fields[key]}")
    lines.append("")
    if history:
        lines += [brain.HISTORY_HEADING, ""] + history
    return "\n".join(lines).rstrip("\n") + "\n"


def _reviews_directory(make: bool) -> Path:
    """brain/reviews/, refused when a write must not go through it; made
    (with brain/) only by a write that is about to happen."""
    _check_directory(create=True, make=make)
    path = brainreviews.directory()
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if make:
            os.mkdir(path)
        return path
    if stat.S_ISLNK(info.st_mode):
        raise Refused("brain/reviews/ is a link; bench never writes through one", 409)
    if not stat.S_ISDIR(info.st_mode):
        raise Refused("brain/reviews/ is not a directory; nothing was written", 409)
    if config.escapes_work(path) is not None:
        raise Refused("brain/reviews/ resolves outside the work root; nothing was written", 409)
    return path


def _plan_review(kind: str, payload: dict, reading: dict, actor: str) -> dict:
    card, review = _review_target(payload)
    number, rid = card["number"], review["id"]
    name = brainreviews.record_name(number, rid)
    path = _reviews_directory(make=False) / name
    data = _current_bytes(path)
    rec = brainreviews.parse_record(name, data) if data is not None else None
    if rec is not None and rec["state"] != "record":
        raise Refused(f"brain/reviews/{name} does not follow the review record format "
                      f"({'; '.join(rec['problems'][:2])}); fix it by hand first", 409)
    now = rec["revision"] if rec else None
    expected = payload.get("expected") or None
    if expected != now:
        raise Refused(
            f"the classification of #{number}/{rid} changed since this page read it (it "
            f"read {str(expected or 'none')[:16]}, the record is now {now or 'none'}). "
            "Nothing was written. Read it again and decide again.", 409, stale=True,
            current=brainreviews.review(number, rid))
    history = brain.split_record(data.decode("utf-8"))["history"] if data is not None else []
    fields = {"Classification": rec["classification"] if rec else "Unclassified",
              "Override": rec["override"] if rec else None,
              "Superseded by": rec["supersededBy"] if rec else None,
              "Invalidated": rec["invalidated"] if rec else None}
    reason = _reason(payload.get("reason"))
    at = _now()
    if kind == "review-classify":
        klass = _classification(payload.get("classification"))
        fields.update({"Classification": klass, "Superseded by": None, "Invalidated": None})
        if klass == "Superseded":
            target = payload.get("supersededBy")
            target = target.strip() if isinstance(target, str) else target
            invalidation = payload.get("invalidation")
            if target and invalidation:
                raise Refused("Superseded names either the replacing entry or an "
                              "invalidation decision, not both", field="supersededBy")
            if target:
                _ids([target], "supersededBy")
                _targets(reading, [target], "supersededBy", self_id=None,
                         allow_tombstone=False)
                fields["Superseded by"] = target
                reason = f"superseded by {target}: {reason}"
            elif invalidation:
                fields["Invalidated"] = _reason(invalidation, "invalidation")
                reason = f"invalidated without a replacement: {reason}"
            else:
                raise Refused("Superseded needs the entry that replaces this review's "
                              "conclusions, or an explicit invalidation decision",
                              field="supersededBy")
        said, what = reason, f"classified {klass}"
    else:
        action = payload.get("action")
        if action not in ("set", "clear"):
            raise Refused("an override is set or cleared", field="action")
        if action == "set":
            fields["Override"] = f"manual · {actor} · {at} — {reason}"
            said = f"manual override set: {reason}"
        else:
            if not fields["Override"]:
                raise Refused(f"#{number}/{rid} has no manual override to clear", 409)
            fields["Override"] = None
            said = f"manual override cleared: {reason}"
        what = f"override {'set' if action == 'set' else 'cleared'}"
    history = history + [_history_line(at, fields["Classification"], actor, said)]
    return {"id": f"#{number}/{rid}", "file": f"{brain.REVIEWS_DIRECTORY}/{name}",
            "expected": now, "text": render_review(number, rid, fields, history),
            "what": what}


# ── the handbook's membership (task 171) ───────────────────────────────
#
# One record, brain/handbook/handbook.md (the format is handbook's), and one
# section of it per write: assign, reorder, replace or remove links in that
# section, bound to the record's exact revision (or its absence) and to a
# request identity, with a reason in its history. It never writes an entry,
# so a link never changes an entry's classification or anything else.

HANDBOOK_FIELDS = {"request", "expected", "section", "entries", "reason"}


def _handbook_directory(make: bool) -> Path:
    """brain/handbook/, refused when a write must not go through it; made
    (with brain/) only by a write that is about to happen."""
    _check_directory(create=True, make=make)
    path = handbook.directory()
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if make:
            os.mkdir(path)
        return path
    if stat.S_ISLNK(info.st_mode):
        raise Refused("brain/handbook/ is a link; bench never writes through one", 409)
    if not stat.S_ISDIR(info.st_mode):
        raise Refused("brain/handbook/ is not a directory; nothing was written", 409)
    if config.escapes_work(path) is not None:
        raise Refused("brain/handbook/ resolves outside the work root; nothing was written", 409)
    return path


def _change_words(before: list[str], after: list[str]) -> str:
    added = [i for i in after if i not in before]
    removed = [i for i in before if i not in after]
    words = []
    if added:
        words.append("added " + ", ".join(added))
    if removed:
        words.append("removed " + ", ".join(removed))
    kept_before = [i for i in before if i in after]
    kept_after = [i for i in after if i in before]
    if kept_before != kept_after:
        words.append("reordered")
    return "; ".join(words) or "unchanged"


def _plan_handbook(payload: dict, reading: dict, actor: str) -> dict:
    for key in payload:
        if key not in HANDBOOK_FIELDS:
            raise Refused(f"{str(key)[:40]!r} cannot be written here: the handbook records which "
                          "entries each section links, and nothing else — an entry is edited on "
                          "its own page", field=str(key)[:40])
    _handbook_directory(make=False)
    rec = handbook.read_record()
    if rec["state"] == "refused":
        raise Refused(f"{rec['file']} was not read: {rec['why']}; nothing was written", 409)
    if rec["state"] in ("invalid", "unsupported"):
        raise Refused(f"{rec['file']} does not follow the handbook format "
                      f"({'; '.join(rec['problems'][:2])}); fix it by hand first — the board "
                      "does not write over a record it cannot read", 409)
    now = rec["revision"]
    expected = payload.get("expected")
    expected = None if expected in (None, "", "none") else expected
    if expected != now:
        raise Refused(
            f"the handbook changed since this page read it (it read {str(expected or 'none')[:16]}, "
            f"the record is now {now or 'none'}). Nothing was written. Read the current "
            "handbook, bring your change across, and save again.", 409, stale=True,
            current=handbook.reading())
    section = payload.get("section")
    if section not in handbook.SECTION_IDS:
        raise Refused("section is one of " + ", ".join(handbook.SECTION_IDS), field="section")
    after = _ids(payload.get("entries"), "entries")
    if len(after) > handbook.MAX_ITEMS:
        raise Refused(f"a section links at most {handbook.MAX_ITEMS} entries", field="entries")
    before = list(rec["sections"].get(section) or [])
    by_id = {e["id"]: e for e in reading["entries"]}
    for entry_id in after:
        if entry_id in before:
            continue    # a link already here may stay while its problem is shown
        if entry_id in reading["duplicates"]:
            raise Refused(f"{entry_id} is claimed by more than one file; fix that first",
                          field="entries")
        found = by_id.get(entry_id)
        if found is None:
            raise Refused(f"{entry_id} is not an entry on this board; a section links existing "
                          "entries only — create it first", field="entries")
        if found["removed"] is not None:
            raise Refused(f"{entry_id} is retired; link the entry that replaced it",
                          field="entries")
    if after == before:
        raise Refused("this would change nothing in the section; nothing was written",
                      field="entries")
    reason = _reason(payload.get("reason"))
    title = dict((s[0], s[1]) for s in handbook.SECTIONS)[section]
    sections = {s: list(rec["sections"].get(s) or []) for s in handbook.SECTION_IDS}
    sections[section] = after
    history = list(rec.get("history") or []) + [
        f"- {_now()} · {actor} — {title}: {_change_words(before, after)}; {reason}"]
    text = handbook.render(sections, history)
    if len(text.encode("utf-8")) > brain.MAX_ENTRY_BYTES:
        raise Refused(f"this save would take {rec['file']} over the {brain.MAX_ENTRY_BYTES}-byte "
                      "limit the board reads, and the board could not read it again. Nothing "
                      "was written; move older lines of its Handbook history out by hand "
                      "(git keeps them), then save again.", 409)
    return {"id": "handbook", "file": f"{brain.HANDBOOK_DIRECTORY}/{handbook.FILE}",
            "expected": now, "text": text,
            "what": f"{section} updated"}


PLANS = {"create": _plan_create, "edit": _plan_edit, "classify": _plan_classify,
         "override": _plan_override, "retire": _plan_retire,
         "handbook": _plan_handbook,
         "review-classify": lambda p, r, a: _plan_review("review-classify", p, r, a),
         "review-override": lambda p, r, a: _plan_review("review-override", p, r, a)}
REVIEW_KINDS = ("review-classify", "review-override")


def _entry_path(entry: dict) -> Path:
    """Where a journalled write lands: one flat file in brain/, or — for a
    review's classification — one record in brain/reviews/, never anything
    a name could carry elsewhere."""
    file = entry.get("file") or ""
    if entry.get("kind") in REVIEW_KINDS:
        sub, _, name = file.partition("/")
        if sub != brain.REVIEWS_DIRECTORY or not brainreviews.RECORD_NAME_RE.match(name):
            raise Refused("the review record's name is not c<card>-<review id>.md", 409)
        return _reviews_directory(make=True) / name
    if entry.get("kind") == "handbook":
        if file != f"{brain.HANDBOOK_DIRECTORY}/{handbook.FILE}":
            raise Refused("the handbook record is brain/handbook/handbook.md", 409)
        return _handbook_directory(make=True) / handbook.FILE
    directory = _check_directory(create=entry.get("kind") == "create")
    if not brain.NAME_RE.match(file):
        raise Refused("the entry's file name is not a plain flat name", 409)
    return directory / file


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
                              current=_current_entry(prior))
            if prior.get("status") == "done":
                return dict(_public(prior), duplicate=True)
            return _finish(request, prior, resumed=True)
        actor = _who()
        reading = brain.reading()
        _check_directory(create=(kind in ("create", "handbook") or kind in REVIEW_KINDS),
                         make=False)
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
    path = _entry_path(entry)
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
                  stale=True, current=_current_entry(entry))


def _current_entry(operation: dict) -> dict | None:
    if operation.get("kind") == "handbook":
        # told by the write's kind: `handbook` is also a legal entry id
        return handbook.reading()
    try:
        return brain.entry(operation.get("id"))
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
