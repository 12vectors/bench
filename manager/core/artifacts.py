"""Retained demonstration outputs: what a check left to look at (task 133).

A passing log says a program exited zero. It does not show what changed.
Some checks can: a browser suite takes screenshots, an API check keeps the
request and response it asserted on, an export check keeps the document
it generated. This module keeps those outputs, and keeps them honestly.

**Where they come from.** Only from a check bench itself ran. A check the
operator's policy declares with `"outputs": true` is handed a directory,
`$BENCH_VERIFY_OUTPUTS`, inside the run's own temporary directory; when
the run ends `verify` asks `ingest` to keep what is there before that
directory is removed. An optional `manifest.json` beside the files says
what each one shows — a caption, the scenario, the viewport, the criterion
it is about, whether it is the *before* or the *after* of a comparison,
what data it used and what it was pointed at. Nothing else writes an
artifact: an agent's sentence, a path in a log or a file in a worktree is
not one.

**What a record says, and how strongly.** Every artifact records its id,
hash, kind, size, the run and check that produced it, the card, the source
revision the run was bound to and whether that source had uncommitted
work, the capture time and the manifest's description. Two strengths are
kept apart:

- *bench-bound* — the run, its check, its outcome and the source it ran
  against. Bench recorded these itself, before anyone was told anything.
- *capture metadata* — what the program said about the picture: the
  scenario, the viewport, the data, the target, and which revision the
  depicted application was. Bench did not watch the application start, so
  "this screenshot shows commit X" is the program's claim, kept and shown
  as one (`depicts.verified` is always false in this version).

**Where they live.** Under `manager/local/state/artifacts/<card>/`, one
`<id>.json` record and one `<id>.bin` beside it — run-owned, machine-local
storage that outlives the run's TMPDIR, a board restart and the card's
worktree, and is served only through the board (`routes`), never by path.
Nothing is committed into the shared ticket history: a large binary or a
runtime output has no business in a repository other people pull, so a
replica that never ran the check holds no record and claims no bytes.

**Bounded, and honest when bounded.** One file may be at most
`BOARD_ARTIFACT_MAX_BYTES`, one run keeps at most `BOARD_ARTIFACT_RUN_FILES`
and one card at most `BOARD_ARTIFACT_CARD_BYTES`; bytes older than
`BOARD_ARTIFACT_RETENTION_DAYS` expire. Expiry and withholding remove the
bytes and keep the record, so "there was a screenshot here, and it is gone"
is said rather than silently forgotten. A record whose bytes are not on
this disk reads `missing` — never offered as a download.

**Safe to look at.** Files are classified by their own bytes, not their
name: PNG, JPEG, GIF and WebP are images; UTF-8 text and JSON are drawn as
text, scrubbed of anything credential-shaped on the way in; everything
else is a download served as `application/octet-stream`. HTML, SVG, XML and
script are never rendered — they are downloads, marked active. An
executable is refused. Links, paths escaping the directory, oversized
files and credential-shaped names are refused and the refusal is recorded.
Scrubbing text is not sanitizing pixels: an image is kept as it was made,
and the record says that nothing inspected it for secrets.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import config
import records
import redact
import taskfiles

VERSION = 1
MARKER = "«redacted»"
MANIFEST = "manifest.json"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_DEPTH = 2

KINDS = ("image", "text", "json", "file")
STATES = {
    "available": "available on this board",
    "expired": "its bytes were removed by the retention rule; the record stays",
    "withheld": "withheld by a person; the record that it existed stays",
    "missing": "the record is here and its bytes are not on this board",
}
ROLES = ("before", "after", "single")
DATA = ("fixture", "mock", "synthetic", "local", "staging", "production",
        "unknown")
TARGETS = ("candidate", "running-service", "unknown")

IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)
EXECUTABLE_MAGIC = (b"\x7fELF", b"MZ", b"#!", b"\xcf\xfa\xed\xfe",
                    b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
ACTIVE_SUFFIXES = (".html", ".htm", ".xhtml", ".svg", ".svgz", ".xml",
                   ".js", ".mjs", ".css", ".swf")
TEXT_SUFFIXES = (".txt", ".log", ".md", ".csv", ".tsv", ".out", ".diff",
                 ".patch", ".ndjson", ".jsonl", ".yaml", ".yml", ".ini")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,119}$")
SECRET_NAME_RE = re.compile(
    r"(?i)(^|[._-])(\.?env|secrets?|credentials?|id_rsa|id_ed25519|"
    r"private[-_]?key|token|passwords?|\.pem|\.key|\.p12|\.pfx)($|[._-])")
ID_RE = re.compile(r"^a-[0-9a-f]{20}$")
TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.md$")
TEXT_FIELDS = {"caption": 300, "scenario": 300, "viewport": 40,
               "criterion": 60, "pair": 60, "environment": 60,
               "revision": 64, "check": 60, "note": 300}

_LOCK = threading.Lock()


class Refused(ValueError):
    """A request about an artifact this board will not answer."""


# ── where a card's artifacts live ──────────────────────────────────────


def _task_ok(task: str) -> bool:
    return bool(isinstance(task, str) and TASK_RE.match(task)
                and Path(task).name == task)


def card_dir(task: str) -> Path:
    if not _task_ok(task):
        raise Refused("not a card")
    return config.ARTIFACTS_DIR / task[:-3]


def _record_path(task: str, artifact_id: str) -> Path:
    if not ID_RE.match(str(artifact_id or "")):
        raise Refused("not an artifact id")
    return card_dir(task) / f"{artifact_id}.json"


def _bytes_path(task: str, artifact_id: str) -> Path:
    if not ID_RE.match(str(artifact_id or "")):
        raise Refused("not an artifact id")
    return card_dir(task) / f"{artifact_id}.bin"


def artifact_id(evidence_id: str, name: str) -> str:
    """One output of one run, always the same id: ingesting a run twice
    keeps one record per file rather than two."""
    digest = hashlib.sha256(f"{evidence_id}\0{name}".encode("utf-8")).hexdigest()
    return "a-" + digest[:20]


# ── what a file is, read from its own bytes ────────────────────────────


def classify(data: bytes, name: str) -> dict:
    """The kind a file is served as. Its bytes decide, never its name — a
    name can only make a file *less* trusted (an `.svg` that happens to be
    text is still a download)."""
    suffix = Path(name).suffix.lower()
    for magic in EXECUTABLE_MAGIC:
        if data.startswith(magic):
            return {"refused": "an executable or script — bench keeps no "
                               "program from a demonstration"}
    if suffix in ACTIVE_SUFFIXES:
        return {"kind": "file", "mime": "application/octet-stream",
                "active": True}
    for magic, ext, mime in IMAGE_SIGNATURES:
        if data.startswith(magic):
            return {"kind": "image", "mime": mime, "active": False, "ext": ext}
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return {"kind": "image", "mime": "image/webp", "active": False,
                "ext": "webp"}
    if b"\0" not in data[:8192]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            head = text.lstrip()[:1]
            if suffix == ".json" or (head in ("{", "[") and suffix in ("", ".json")):
                try:
                    json.loads(text)
                    return {"kind": "json", "mime": "application/json",
                            "active": False}
                except ValueError:
                    pass
            if re.search(r"(?i)<\s*(script|html|svg|iframe|object)\b", text[:65536]):
                return {"kind": "file", "mime": "application/octet-stream",
                        "active": True}
            if suffix in TEXT_SUFFIXES or suffix == "":
                return {"kind": "text", "mime": "text/plain; charset=utf-8",
                        "active": False}
    return {"kind": "file", "mime": "application/octet-stream", "active": False}


# ── the manifest a demonstration program may write ─────────────────────


def _clean(value, cap: int) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = " ".join(str(value).split())[:cap]
    text = redact.scrub(text, MARKER)
    return text or None


def read_manifest(outdir: Path) -> dict:
    """`manifest.json`, if the program wrote one: what each file shows.
    Malformed is said, never fatal — the files are still kept, described
    by their names."""
    found = {"entries": {}, "summary": None, "partial": False, "problems": []}
    path = outdir / MANIFEST
    try:
        if path.is_symlink() or not path.is_file():
            return found
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            found["problems"].append("manifest.json is over 256 KiB and was not read")
            return found
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        found["problems"].append("manifest.json could not be read as JSON")
        return found
    if not isinstance(data, dict):
        found["problems"].append("manifest.json is not an object")
        return found
    found["summary"] = _clean(data.get("summary"), 600)
    found["partial"] = data.get("partial") is True
    for entry in data.get("outputs") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            found["problems"].append("an outputs entry names no file")
            continue
        meta = {key: _clean(entry.get(key), cap) for key, cap in TEXT_FIELDS.items()}
        role = str(entry.get("role") or "single").lower()
        meta["role"] = role if role in ROLES else "single"
        data_word = str(entry.get("data") or "unknown").lower()
        meta["data"] = data_word if data_word in DATA else "unknown"
        target = str(entry.get("target") or "unknown").lower()
        meta["target"] = target if target in TARGETS else "unknown"
        meta["partial"] = entry.get("partial") is True
        found["entries"][entry["file"].strip().lstrip("./")] = meta
    return found


def _walk(outdir: Path) -> list[tuple[str, Path]]:
    """Every entry under the directory, two levels deep, as (relative
    name, path). Links are returned so they can be refused by name."""
    out: list[tuple[str, Path]] = []

    def visit(directory: Path, prefix: str, depth: int) -> None:
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for path in entries:
            rel = f"{prefix}{path.name}"
            if path.is_symlink():
                out.append((rel, path))
            elif path.is_dir():
                if depth < MAX_DEPTH:
                    visit(path, rel + "/", depth + 1)
            elif rel != MANIFEST:
                out.append((rel, path))

    visit(outdir, "", 1)
    return out


# ── keeping what a run left ────────────────────────────────────────────


def criterion_rev(task: str, criterion: str) -> str | None:
    """The wording of the criterion an output names, as it was when the
    output was kept — the same revision `acceptance.criterion_rev` gives a
    decision, so a reworded criterion does not silently inherit an
    output made about its old wording. None when the card does not have
    that criterion."""
    stage = taskfiles.find_stage_of(task)
    if not stage:
        return None
    try:
        text = (config.TASKS / stage / task).read_text(encoding="utf-8")
    except OSError:
        return None
    for item in taskfiles.criteria(taskfiles.card_parts(text)["brief"]):
        if item.get("id") == criterion:
            return hashlib.sha256(" ".join(item["text"].split()).encode(
                "utf-8")).hexdigest()[:12]
    return None


def ingest(outdir, *, task: str, run: dict) -> dict:
    """Keep what one check run wrote to its outputs directory.

    `run` is bench's own account of the run — its evidence id, check,
    scope, outcome, bound source, result identity and who asked — and is
    what makes the record *bench-bound*. Returns the ids kept and every
    file refused with why. Never raises for a bad file: a demonstration
    that could not be kept is a fact about the run, not a runner failure.
    """
    outdir = Path(outdir)
    result = {"kept": [], "refused": [], "problems": [], "summary": None,
              "partial": False}
    if not _task_ok(task) or not outdir.is_dir() or outdir.is_symlink():
        return result
    manifest = read_manifest(outdir)
    result.update(summary=manifest["summary"], partial=manifest["partial"],
                  problems=list(manifest["problems"]))
    try:
        base = outdir.resolve(strict=True)
    except OSError:
        return result
    evidence_id = str(run.get("evidence") or "")
    source = run.get("source") or {}
    now = time.time()
    kept = 0
    for rel, path in _walk(outdir):
        def refuse(why: str) -> None:
            result["refused"].append({"name": rel[:120], "why": why})
        if path.is_symlink():
            refuse("a link — bench keeps no output that points elsewhere")
            continue
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(base)
            info = path.lstat()
        except (OSError, ValueError):
            refuse("outside the outputs directory")
            continue
        name = path.name
        if SECRET_NAME_RE.search(name):
            refuse("its name reads as a credential")
            continue
        if not NAME_RE.match(name) or ".." in rel:
            refuse("a name bench will not serve (letters, digits, '.', '_', "
                   "'-' and spaces, at most 120 characters)")
            continue
        if info.st_size > config.ARTIFACT_MAX_BYTES:
            refuse(f"{info.st_size} bytes, over BOARD_ARTIFACT_MAX_BYTES "
                   f"({config.ARTIFACT_MAX_BYTES})")
            continue
        if kept >= config.ARTIFACT_RUN_FILES:
            refuse(f"over the {config.ARTIFACT_RUN_FILES} files one run keeps "
                   f"(BOARD_ARTIFACT_RUN_FILES)")
            continue
        try:
            data = path.read_bytes()
        except OSError:
            refuse("could not be read")
            continue
        kind = classify(data, name)
        if kind.get("refused"):
            refuse(kind["refused"])
            continue
        scrubbed = 0
        if kind["kind"] in ("text", "json"):
            text = data.decode("utf-8")
            clean = redact.scrub(text, MARKER)
            if clean != text:
                scrubbed = len(redact.found(text)) or 1
                data = clean.encode("utf-8")
                if kind["kind"] == "json":
                    try:
                        json.loads(clean)
                    except ValueError:
                        kind = {"kind": "text", "mime": "text/plain; charset=utf-8",
                                "active": False}
        meta = manifest["entries"].get(rel) or manifest["entries"].get(name) or {}
        aid = artifact_id(evidence_id, rel)
        criterion = meta.get("criterion")
        depicted = meta.get("revision")
        record = {
            "v": VERSION, "id": aid, "task": task, "name": name, "path": rel,
            "kind": kind["kind"], "mime": kind["mime"],
            "active": bool(kind.get("active")),
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "capturedAt": now, "state": "available",
            "caption": meta.get("caption") or None,
            "scenario": meta.get("scenario"), "viewport": meta.get("viewport"),
            "criterion": criterion,
            "criterionRev": criterion_rev(task, criterion) if criterion else None,
            "role": meta.get("role") or "single",
            "pair": meta.get("pair"), "data": meta.get("data") or "unknown",
            "environment": meta.get("environment"),
            "target": meta.get("target") or "unknown",
            "note": meta.get("note"),
            "partial": bool(meta.get("partial") or manifest["partial"]),
            "described": bool(meta),
            "scrubbed": scrubbed,
            "pixelsInspected": False if kind["kind"] == "image" else None,
            # bench's own account of the run that made it
            "producer": {
                "evidence": evidence_id or None, "check": run.get("check"),
                "scope": run.get("scope"), "outcome": run.get("outcome"),
                "reason": run.get("reason"), "resultId": run.get("resultId"),
                "agent": run.get("agent"), "intent": run.get("intent"),
                "actor": run.get("actor"), "root": run.get("root"),
            },
            "source": {
                "commit": source.get("commit"), "branch": source.get("branch"),
                "dirty": bool(source.get("dirty")),
                "bound": bool(source.get("bound")),
                "sourceId": source.get("sourceId"),
            },
            # what the program said about the picture: a claim, kept as one
            "depicts": {
                "revision": depicted or source.get("commit"),
                "claimed": bool(depicted),
                "verified": False,
                "why": ("bench ran the program against the source above and did "
                        "not observe which build the output shows"),
            },
        }
        with _LOCK:
            records.write_bytes(_bytes_path(task, aid), data, durable=True)
            records.write_json(_record_path(task, aid), record, durable=True)
        kept += 1
        result["kept"].append(aid)
    if result["kept"]:
        sweep(task)
    return result


# ── reading them back ──────────────────────────────────────────────────


def state_of(record: dict) -> str:
    """What can be done with a record's bytes now, on this board."""
    word = record.get("state")
    if word in ("expired", "withheld"):
        return word
    try:
        path = _bytes_path(record["task"], record["id"])
    except (Refused, KeyError):
        return "missing"
    if path.is_symlink() or not path.is_file():
        return "missing"
    return "available"


def count(task: str) -> int:
    """How many records this board keeps for a card, by name alone: the
    board-wide reading asks this on every state read, so it opens no
    record. The card's own reading (`for_task`) is what validates them."""
    try:
        directory = card_dir(task)
    except Refused:
        return 0
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.glob("a-*.json")
               if ID_RE.match(path.stem))


def for_task(task: str) -> list[dict]:
    """Every record this board keeps for a card, oldest first. Reading is
    reading: no bytes are hashed, nothing is swept, nothing written."""
    try:
        directory = card_dir(task)
    except Refused:
        return []
    if not directory.is_dir():
        return []
    found = []
    for path in directory.glob("a-*.json"):
        data = records.read_json(path, None, expect=dict)
        if data and data.get("task") == task and ID_RE.match(str(data.get("id"))):
            data["state"] = state_of(data)
            found.append(data)
    found.sort(key=lambda r: (r.get("capturedAt") or 0, r.get("path") or ""))
    return found


def read(task: str, artifact_id: str) -> dict | None:
    path = _record_path(task, artifact_id)
    data = records.read_json(path, None, expect=dict)
    if not data or data.get("id") != artifact_id or data.get("task") != task:
        return None
    data["state"] = state_of(data)
    return data


def content(task: str, artifact_id: str) -> tuple[dict, bytes]:
    """The record and its bytes, for serving. Refused rather than guessed:
    an unknown id, a record whose bytes are gone, a link where the bytes
    should be, and bytes that no longer hash to what was kept."""
    record = read(task, artifact_id)
    if record is None:
        raise Refused("no such artifact on this board")
    if record["state"] != "available":
        raise Refused(f"not available: {STATES.get(record['state'], record['state'])}")
    path = _bytes_path(task, artifact_id)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(config.ARTIFACTS_DIR.resolve(strict=True))
        data = path.read_bytes()
    except (OSError, ValueError):
        raise Refused("not available: the bytes could not be read on this board")
    if hashlib.sha256(data).hexdigest() != record.get("sha256"):
        raise Refused("not available: the bytes on disk are not the bytes bench "
                      "kept, so they are not served")
    return record, data


# ── withholding and retention ──────────────────────────────────────────


def _drop_bytes(task: str, artifact_id: str) -> None:
    try:
        _bytes_path(task, artifact_id).unlink()
    except FileNotFoundError:
        pass


def withhold(task: str, artifact_id: str, *, actor: str, reason: str) -> dict:
    """A person removes an artifact's bytes and what describes it. The
    record that something existed — id, kind, size, hash, the run and
    when — stays, with who withheld it and why."""
    reason = " ".join(str(reason or "").split())[:300]
    if len(reason) < 3:
        raise Refused("say why it is withheld (three characters at least)")
    reason = redact.scrub(reason, MARKER)
    with _LOCK:
        record = read(task, artifact_id)
        if record is None:
            raise Refused("no such artifact on this board")
        if record.get("state") == "withheld":
            return record
        _drop_bytes(task, artifact_id)
        for key in ("caption", "scenario", "note", "viewport", "environment"):
            record[key] = None
        record["name"] = "withheld"
        record["state"] = "withheld"
        record["withheld"] = {"actor": str(actor or "unknown")[:80],
                              "at": time.time(), "reason": reason}
        records.write_json(_record_path(task, artifact_id), record, durable=True)
    return record


def sweep(task: str | None = None, now: float | None = None) -> list[str]:
    """Apply the retention rule: bytes older than the retention period
    expire, then a card over its byte cap loses its oldest bytes until it
    fits. Records are kept and marked expired with the reason."""
    now = time.time() if now is None else now
    limit = config.ARTIFACT_RETENTION_DAYS * 86400
    expired: list[str] = []
    tasks = [task] if task else (
        [p.name + ".md" for p in config.ARTIFACTS_DIR.iterdir() if p.is_dir()]
        if config.ARTIFACTS_DIR.is_dir() else [])
    with _LOCK:
        for name in tasks:
            live = [r for r in for_task(name) if r["state"] == "available"]
            total = sum(int(r.get("size") or 0) for r in live)
            for record in live:
                why = None
                if now - float(record.get("capturedAt") or 0) > limit:
                    why = (f"older than {config.ARTIFACT_RETENTION_DAYS:g} days "
                           f"(BOARD_ARTIFACT_RETENTION_DAYS)")
                elif total > config.ARTIFACT_CARD_BYTES:
                    why = (f"the card was over {config.ARTIFACT_CARD_BYTES} bytes "
                           f"(BOARD_ARTIFACT_CARD_BYTES); the oldest go first")
                if why is None:
                    continue
                total -= int(record.get("size") or 0)
                _drop_bytes(name, record["id"])
                record["state"] = "expired"
                record["expired"] = {"at": now, "why": why}
                records.write_json(_record_path(name, record["id"]), record,
                                   durable=True)
                expired.append(record["id"])
    return expired


# ── what the page is sent ──────────────────────────────────────────────

PUBLIC = ("id", "task", "name", "kind", "mime", "active", "sha256", "size",
          "capturedAt", "state", "caption", "scenario", "viewport",
          "criterion", "role", "pair", "data", "environment", "target",
          "note", "partial", "described", "scrubbed", "pixelsInspected",
          "producer", "source", "depicts", "withheld", "expired")


def public(record: dict) -> dict:
    """An allowlist, so a field added to the record later does not reach a
    reader by default. Never a path on this disk."""
    out = {key: record.get(key) for key in PUBLIC}
    out["stateWords"] = STATES.get(out["state"], out["state"])
    out["url"] = (f"/api/artifact?card={record['task']}&id={record['id']}"
                  if out["state"] == "available" else None)
    return out
