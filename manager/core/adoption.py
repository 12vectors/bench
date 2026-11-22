"""Adopting legacy `reference/` material into the Brain (task 172): what is
there, what adopting one file would do, and the frozen facts a write is
held to.

`reference/` is deprecated for new knowledge and stays exactly where it is:
every file keeps its bytes and its `/files/reference/<name>` address. This
module reads; `brainwrite` is still the Brain's one writer, and its `adopt`
write asks `freeze` here, under its lock, immediately before it plans.

- **Two choices, one entry each.** `catalogue` records a retained file as an
  `Attachment` entry with a stable id; `explanation` is a maintained entry a
  person wrote or edited, citing the file and its catalogue entry. A third,
  `refresh`, records that a person looked at a source that changed; it
  changes one header field, a binding and the history line, never the prose.
- **Identity is the source's.** An adopted entry's id is derived from the
  file's name and the role, so a double click, a retry, a restart or a second
  board all arrive at the same entry rather than a second one.
- **Nothing is asserted.** Adopted material starts Unclassified. Cataloguing
  says where a file is and what its bytes were, never that it is right.
- **Reading is passive.** The inventory and the preview read `reference/`,
  the cards, `plans/`, `brain/` and local git metadata. They write, fetch,
  launch and run nothing.
- **Refused, not guessed.** A link, a non-regular file, a credential-shaped
  name or content, an oversized file and a file whose bytes contradict its
  name are listed with why, and cannot be adopted. Credential detection is
  pattern matching: it reduces the risk and does not prove a file clean.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
from pathlib import Path

import brain
import config
import gitrepo
import redact
import taskfiles

CONTRACT = 1
DIRECTORY = "reference"
MAX_ITEMS = 500
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SEED_BYTES = 200 * 1024         # the most a maintained body may hold
SCAN_BYTES = 2 * 1024 * 1024        # how much of a file the secret scan reads
MAX_LINK_FILES = 3000
MAX_LINK_BYTES = 1024 * 1024
MAX_HOLDERS = 12
OPERATIONS = ("catalogue", "explanation", "refresh")
EXPLANATION_KINDS = ("Guide", "Investigation", "Decision")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_LINK_RE = re.compile(r"reference/([A-Za-z0-9][A-Za-z0-9._-]{0,120})")
_H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$")
_CREDENTIAL_NAME = re.compile(
    r"(^|[._-])(env|netrc|npmrc|pypirc|htpasswd)($|[._-])|secret|credential|passw|"
    r"(^|[._-])tokens?($|[._-])|\.(pem|key|p12|pfx|keystore|jks|asc)$|^id_(rsa|dsa|ecdsa|ed25519)",
    re.IGNORECASE)
_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image"), (b"\xff\xd8\xff", "image"),
               (b"GIF87a", "image"), (b"GIF89a", "image"), (b"%PDF-", "pdf"))
_DECLARED = {".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
             ".webp": "image", ".pdf": "pdf"}
_TEXT_SUFFIX = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".json": "text",
                ".csv": "text", ".yaml": "text", ".yml": "text", ".html": "html",
                ".htm": "html", ".svg": "svg"}

_MEMO: dict[tuple, dict] = {}
_MEMO_LOCK = threading.Lock()


class Stop(Exception):
    """An adoption bench will not make, with the status the API answers and
    any fields the page needs (`code`, `stale`)."""

    def __init__(self, message: str, status: int = 409, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


class NotAName(ValueError):
    """The string cannot name a file in `reference/`."""


# ── identity ───────────────────────────────────────────────────────────


def entry_id(role: str, name: str) -> str:
    """The stable id of the entry adopting this file in this role."""
    digest = hashlib.sha256(f"{DIRECTORY}/{name}".encode()).hexdigest()[:16]
    return f"a-{'ref' if role == 'catalogue' else 'exp'}-{digest}"


def field(name: str, digest: str, role: str) -> str:
    """The `**Adopted from:**` value: which file, which bytes, which role."""
    return f"{DIRECTORY}/{name}@{digest} · {role}"


def address(name: str) -> str:
    return f"/files/{DIRECTORY}/{name}"


def catalogue_body(name: str) -> str:
    """A catalogue entry's body: where the file is, and what cataloguing
    does not say. Its digest lives in the header, so a later refresh never
    has to touch prose a person may have edited."""
    return (f"The retained file `{DIRECTORY}/{name}`, catalogued unchanged. "
            f"[Open the original]({address(name)}).\n\n"
            "Cataloguing records where this file is and what its bytes were. It does not "
            "assess whether what the file says is current or correct.")


# ── one file ───────────────────────────────────────────────────────────


def _directory_state() -> tuple[str, str | None]:
    path = config.work_root() / DIRECTORY
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing", None
    except OSError as exc:
        return "refused", f"reference/ could not be examined ({exc.strerror or exc})"
    if stat.S_ISLNK(info.st_mode):
        return "refused", "reference/ is a link, and a link is never followed"
    if not stat.S_ISDIR(info.st_mode):
        return "refused", "reference/ is not a directory"
    if config.escapes_work(path) is not None:
        return "refused", "reference/ resolves outside the work root"
    return "present", None


def _type_of(name: str, head: bytes, utf8: bool) -> tuple[str, str | None]:
    """What the bytes are, and why they cannot be adopted when they
    contradict the name. The bytes decide; a suffix only makes a claim."""
    suffix = Path(name).suffix.lower()
    seen = next((kind for magic, kind in _SIGNATURES if head.startswith(magic)), None)
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        seen = "image"
    declared = _DECLARED.get(suffix)
    if declared and seen != declared:
        return "malformed", f"it is named {suffix} and its bytes are not a {declared}"
    if seen:
        return seen, None
    claimed = _TEXT_SUFFIX.get(suffix)
    if claimed and not utf8:
        return "malformed", f"it is named {suffix} and is not UTF-8 text"
    if claimed:
        return claimed, None
    return ("text", None) if utf8 else ("binary", None)


def _read_facts(path: Path) -> dict:
    """Size, digest and type from one open of the file, never through a
    link. Raises OSError with the reason it cannot be read."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("it is not a regular file")
        if info.st_size > MAX_SOURCE_BYTES:
            raise OSError(f"it is {info.st_size} bytes, over the "
                          f"{MAX_SOURCE_BYTES // (1024 * 1024)} MiB limit")
        key = (str(path), info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        with _MEMO_LOCK:
            known = _MEMO.get(key)
        if known is not None:
            return dict(known)
        sha, kept, total = hashlib.sha256(), bytearray(), 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise OSError("it grew past the size limit while being read")
            sha.update(chunk)
            if len(kept) < SCAN_BYTES:
                kept += chunk[:SCAN_BYTES - len(kept)]
    finally:
        os.close(fd)
    data = bytes(kept)
    # a multi-byte character cut by the scan window is not a failure
    utf8 = b"\0" not in data and _utf8(data, windowed=total > SCAN_BYTES)
    kind, wrong = _type_of(path.name, data[:16], utf8)
    shapes = redact.found(data.decode("utf-8" if utf8 else "latin-1", "ignore"))
    facts = {"size": total, "sha256": sha.hexdigest(), "type": kind, "malformed": wrong,
             "secret": shapes[:3], "scanned": min(total, SCAN_BYTES)}
    with _MEMO_LOCK:
        if len(_MEMO) > MAX_ITEMS * 2:
            _MEMO.clear()
        _MEMO[key] = dict(facts)
    return facts


def _utf8(data: bytes, *, windowed: bool) -> bool:
    """Valid UTF-8; for a window of a larger file, but for a last character
    the window cut."""
    for trim in range(0, 4 if windowed else 1):
        try:
            data[:len(data) - trim].decode("utf-8")
            return True
        except UnicodeDecodeError:
            continue
    return False


def _inspect(name: str) -> dict:
    """One name in `reference/`, said as what it is and whether it may be
    adopted: `state` is `ready`, `excluded` or `unsupported` here; the
    entries that already adopt it refine `ready` afterwards."""
    shown = brain.clean(name, 130)
    item = {"name": shown, "address": None, "type": None, "size": None, "sha256": None,
            "state": "ready", "why": None}
    if name.startswith("."):
        return {**item, "state": "excluded", "why": "hidden files are not adopted"}
    if not NAME_RE.match(name):
        return {**item, "state": "unsupported",
                "why": "its name cannot be cited as a source: use letters, digits, '.', '_' and "
                       "'-' (renaming it is a separate decision; its address is unchanged)"}
    if _CREDENTIAL_NAME.search(name):
        return {**item, "state": "excluded", "why": "its name looks like a credential, so it "
                "was not opened"}
    path = config.work_root() / DIRECTORY / name
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            return {**item, "state": "excluded", "why": "it is a link, and a link is never "
                    "followed"}
        if stat.S_ISDIR(info.st_mode):
            return {**item, "state": "unsupported", "type": "directory",
                    "why": "it is a directory; only files directly in reference/ have an address"}
        if not stat.S_ISREG(info.st_mode):
            return {**item, "state": "excluded", "why": "it is not a regular file"}
        facts = _read_facts(path)
    except FileNotFoundError:
        return {**item, "state": "missing", "why": "there is no such file in reference/"}
    except OSError as exc:
        return {**item, "state": "excluded", "why": exc.strerror or str(exc)}
    item.update(address=address(name), type=facts["type"], size=facts["size"],
                sha256=facts["sha256"])
    if facts["secret"]:
        # the digest of a credential-bearing file is not published either
        return {**item, "state": "excluded", "sha256": None, "address": None,
                "why": "its content looks like it holds a credential ("
                       + ", ".join(facts["secret"]) + "), so it cannot be adopted"}
    if facts["malformed"]:
        return {**item, "state": "unsupported", "why": facts["malformed"]}
    return item


# ── what surrounds it ──────────────────────────────────────────────────


def _inbound() -> tuple[dict[str, list[dict]], bool]:
    """Which cards, plans and Brain entries name a `reference/` file, read
    from their text. Bounded: the answer says when it stopped early."""
    places: list[tuple[str, Path]] = []
    for slug, _ in config.STAGES:
        places.append(("card", config.TASKS / slug))
    places.append(("card", config.TASKS / "archive"))
    places.append(("plan", config.work_root() / "plans"))
    places.append(("brain", brain.directory()))
    out: dict[str, list[dict]] = {}
    read, truncated = 0, False
    for kind, place in places:
        try:
            if stat.S_ISLNK(os.lstat(place).st_mode):
                continue
            names = sorted(os.listdir(place))
        except OSError:
            continue
        for file in names:
            if not file.endswith((".md", ".html", ".txt")) or file.startswith("."):
                continue
            if read >= MAX_LINK_FILES:
                truncated = True
                break
            read += 1
            try:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                fd = os.open(place / file, flags)
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        continue
                    data = os.read(fd, MAX_LINK_BYTES)
                finally:
                    os.close(fd)
            except OSError:
                continue
            found = {m.rstrip(".") for m in _LINK_RE.findall(data.decode("utf-8", "ignore"))}
            if not found:
                continue
            holder = {"kind": kind, "name": brain.clean(file, 130)}
            if kind == "card":
                number = taskfiles.NUMBER_RE.match(file)
                holder["address"] = (f"/cards/{taskfiles.canonical_number(number.group(1))}"
                                     if number else None)
            elif kind == "plan":
                holder["address"] = f"/files/plans/{file}" if NAME_RE.match(file) else None
            else:
                holder["address"] = None
            for name in found:
                out.setdefault(name, []).append(holder)
    return out, truncated


def _repository() -> tuple[Path | None, str]:
    """The work-state repository, and what the work root's paths carry as a
    prefix inside it."""
    root = gitrepo.state()
    if root is None:
        return None, ""
    try:
        rel = os.path.relpath(Path(config.work_root()).resolve(), Path(root).resolve())
    except ValueError:
        return None, ""
    return Path(root), "" if rel == "." else rel.replace(os.sep, "/") + "/"


def _ownership() -> dict[str, dict]:
    """Who last committed each `reference/` file, and whether the file on
    disk is that commit. Local git metadata only; an untracked file, or a
    work root in no repository, has no known owner."""
    repo, prefix = _repository()
    if repo is None:
        return {}
    where = f"{prefix}{DIRECTORY}/"
    log = gitrepo.git(repo, "log", "-n", "2000", "--no-renames", "--name-only",
                      "--format=%x01%H%x1f%an%x1f%aI", "--", where, timeout=10)
    out: dict[str, dict] = {}
    if log.returncode == 0:
        current = None
        for line in log.stdout.splitlines():
            if line.startswith("\x01"):
                parts = line[1:].split("\x1f")
                current = parts if len(parts) == 3 else None
            elif current and line.startswith(where):
                name = line[len(where):]
                if name not in out:
                    out[name] = {"state": "known", "name": brain.clean(current[1], 80),
                                 "commit": current[0], "at": current[2], "dirty": False}
    status = gitrepo.git(repo, "status", "--porcelain", "--no-renames", "--", where, timeout=10)
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            path = line[3:].strip().strip('"')
            if path.startswith(where) and path[len(where):] in out:
                out[path[len(where):]]["dirty"] = True
    return out


def _claims(reading: dict, name: str) -> dict:
    """The entries that adopt or merely cite this file."""
    claims: dict = {"catalogue": [], "explanation": [], "citedBy": []}
    for e in reading["entries"]:
        adopted = e.get("adopted")
        if adopted and adopted["name"] == name:
            claims[adopted["role"]].append(e)
        elif f"{DIRECTORY}/{name}" in e["sources"]:
            claims["citedBy"].append({"id": e["id"], "title": e["title"]})
    return claims


def _public_entry(e: dict) -> dict:
    return {"id": e["id"], "title": e["title"], "classification": e["classification"],
            "revision": e["revision"], "retired": e["removed"] is not None,
            "override": e["override"] is not None, "recorded": e["adopted"]["sha256"]}


def _describe(item: dict, reading: dict, links: dict, owners: dict) -> dict:
    name = item["name"]
    holders = links.get(name, [])
    owner = owners.get(name) or {"state": "unknown"}
    out = {**item, "inbound": holders[:MAX_HOLDERS], "inboundCount": len(holders),
           "owner": {k: v for k, v in owner.items() if k != "commit"}}
    claims = _claims(reading, name)
    catalogue = claims["catalogue"]
    explanation = claims["explanation"]
    out["catalogue"] = _public_entry(catalogue[0]) if catalogue else None
    out["explanation"] = _public_entry(explanation[0]) if explanation else None
    out["citedBy"] = claims["citedBy"][:MAX_HOLDERS]
    if item["state"] == "missing" and catalogue:
        out["state"] = "source-missing"
        out["why"] = "an entry adopts this file and the file is no longer in reference/"
    if item["state"] != "ready":
        return out
    if len(catalogue) > 1 or len(explanation) > 1:
        out.update(state="conflict", why="more than one entry claims this file in one role; "
                   "settle them by hand")
    elif catalogue and catalogue[0]["removed"] is not None:
        out.update(state="retired", why="its catalogue entry was retired; the id is not reused")
    elif any(e["adopted"]["sha256"] != item["sha256"]
             for e in catalogue + explanation if e["removed"] is None):
        out.update(state="changed", why="the file's bytes differ from what its entry recorded")
    elif catalogue:
        out["state"] = "catalogued"
    return out


# ── the two readings ───────────────────────────────────────────────────


def inventory() -> dict:
    """Every name directly in `reference/`, read and written nowhere."""
    state, why = _directory_state()
    base = {"kind": "bench-brain-adoption", "v": CONTRACT,
            "directory": {"state": state, "why": why, "path": f"{DIRECTORY}/"},
            "items": [], "truncated": False, "linksTruncated": False,
            "limits": {"files": MAX_ITEMS, "bytes": MAX_SOURCE_BYTES}}
    if state != "present":
        return base
    try:
        names = sorted(n for n in os.listdir(config.work_root() / DIRECTORY) if n != ".gitkeep")
    except OSError as exc:
        base["directory"].update(state="refused", why=f"reference/ could not be listed ({exc})")
        return base
    reading = brain.reading()
    links, cut = _inbound()
    owners = _ownership()
    base["truncated"] = len(names) > MAX_ITEMS
    base["linksTruncated"] = cut
    base["items"] = [_describe(_inspect(n), reading, links, owners) for n in names[:MAX_ITEMS]]
    return base


def _check_name(name) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise NotAName(f"{str(name)[:70]!r} is not a file name in reference/")
    return name


def _seed(name: str, item: dict) -> str | None:
    """A Markdown or text file's own words, offered as the starting point
    of a maintained explanation. HTML, prototypes and binaries are never
    converted: their explanation is written by a person."""
    if item.get("type") not in ("markdown", "text") or (item.get("size") or 0) > MAX_SEED_BYTES:
        return None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(config.work_root() / DIRECTORY / name, flags)
        try:
            data = os.read(fd, MAX_SEED_BYTES + 1)
        finally:
            os.close(fd)
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            return None
        return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeDecodeError):
        return None


def _title_from(name: str, seed: str | None) -> str:
    """The file's first heading when it is safe to reuse as plain text;
    otherwise its name. A person may change either before Apply."""
    for line in (seed or "").split("\n")[:40]:
        match = _H1_RE.match(line)
        if match:
            title = " ".join(match.group(1).split())
            if title and title == brain.clean(title, brain.MAX_TITLE_CHARS) and \
                    not re.search(r"[<>]", title) and not title.startswith("#"):
                return title
            break
    return name


def _recorded_text(name: str, e: dict | None) -> str | None:
    """The bytes an entry recorded, when git still holds them at the commit
    it bound. Used to show what changed; never written anywhere."""
    if not e:
        return None
    repo, prefix = _repository()
    if repo is None:
        return None
    target = f"{DIRECTORY}/{name}"
    for binding in e.get("bindings") or []:
        if binding.get("type") == "work" and binding.get("target") == target:
            shown = gitrepo.git(repo, "show", f"{binding['revision']}:{prefix}{target}",
                                timeout=10)
            if shown.returncode == 0 and hashlib.sha256(
                    shown.stdout.encode("utf-8")).hexdigest() == e["adopted"]["sha256"]:
                return shown.stdout
    return None


def _commit_of(name: str) -> str | None:
    """The commit holding exactly these bytes, or None for a file that is
    untracked or has uncommitted changes: no binding is better than one
    that names different bytes."""
    owner = _ownership().get(name)
    return owner["commit"] if owner and not owner.get("dirty") else None


def preview(name) -> dict:
    """What adopting this one file would do, per choice. A read: nothing
    is written, fetched, launched or run. Raises `NotAName`."""
    name = _check_name(name)
    state, why = _directory_state()
    if state != "present":
        return {"kind": "bench-brain-adoption-preview", "v": CONTRACT, "name": name,
                "state": "refused" if state == "refused" else "missing",
                "why": why or "there is no reference/ directory", "choices": []}
    reading = brain.reading()
    links, _ = _inbound()
    item = _describe(_inspect(name), reading, links, _ownership())
    out = {"kind": "bench-brain-adoption-preview", "v": CONTRACT, **item, "choices": [],
           "sourceText": None, "previousText": None}
    if item["state"] not in ("ready", "catalogued", "changed"):
        return out
    seed = _seed(name, item)
    out["sourceText"] = seed
    claims = _claims(reading, name)
    catalogue = claims["catalogue"][0] if claims["catalogue"] else None
    explanation = claims["explanation"][0] if claims["explanation"] else None
    title = _title_from(name, seed)
    out["choices"].append({
        "op": "catalogue", "default": catalogue is None, "available": catalogue is None,
        "why": None if catalogue is None else "already catalogued as " + catalogue["id"],
        "destination": {"id": entry_id("catalogue", name),
                        "revision": catalogue["revision"] if catalogue else "none"},
        "defaults": {"title": title, "kind": "Attachment", "summary": "",
                     "body": catalogue_body(name)}})
    blocked = ("catalogue this file first: the explanation names the retained source"
               if catalogue is None else
               "already explained by " + explanation["id"] if explanation else
               "catalogue the changed file first (Record the new bytes)"
               if catalogue["adopted"]["sha256"] != item["sha256"] else None)
    out["choices"].append({
        "op": "explanation", "default": False, "available": blocked is None, "why": blocked,
        "destination": {"id": entry_id("explanation", name),
                        "revision": explanation["revision"] if explanation else "none"},
        "defaults": {"title": title, "kind": "Guide", "summary": "", "body": seed or ""},
        "seeded": seed is not None})
    stale = [e for e in (catalogue, explanation)
             if e and e["removed"] is None and e["adopted"]["sha256"] != item["sha256"]]
    for e in stale:
        previous = _recorded_text(name, e)
        if previous is not None and out["previousText"] is None:
            out["previousText"] = previous
        out["choices"].append({
            "op": "refresh", "default": False, "available": True, "why": None,
            "role": e["adopted"]["role"],
            "destination": {"id": e["id"], "revision": e["revision"]},
            "recorded": e["adopted"]["sha256"], "body": e["body"],
            "previousAvailable": previous is not None})
    return out


def about(e: dict) -> dict | None:
    """An adopted entry's relationship to its file, read now: the bytes it
    recorded against the bytes there, and its counterpart entry."""
    adopted = e.get("adopted")
    if not adopted:
        return None
    item = _inspect(adopted["name"])
    now = item.get("sha256")
    state = ("missing" if item["state"] == "missing" else
             "unreadable" if not now else
             "unchanged" if now == adopted["sha256"] else "changed")
    other = "explanation" if adopted["role"] == "catalogue" else "catalogue"
    counterpart = None
    for candidate in brain.reading()["entries"]:
        found = candidate.get("adopted")
        if found and found["name"] == adopted["name"] and found["role"] == other:
            counterpart = {"id": candidate["id"], "title": candidate["title"], "role": other,
                           "classification": candidate["classification"]}
            break
    return {"name": adopted["name"], "role": adopted["role"], "recorded": adopted["sha256"],
            "current": now, "state": state, "address": address(adopted["name"]),
            "counterpart": counterpart}


# ── what a write is held to ────────────────────────────────────────────


def freeze(name, op, source, expected, reading: dict) -> dict:
    """The source and destination an Apply named, checked again now, under
    the writer's lock. Raises `Stop` naming what moved; nothing is written."""
    if op not in OPERATIONS:
        raise Stop("an adoption is a catalogue, an explanation or a refresh", 400, field="op")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise Stop("that is not a file name in reference/", 400, field="name")
    if not isinstance(source, str) or not DIGEST_RE.match(source):
        raise Stop("an adoption names the exact bytes its preview showed", 400, field="source")
    state, why = _directory_state()
    if state != "present":
        raise Stop(why or "there is no reference/ directory", 409, code="source-unavailable")
    item = _inspect(name)
    if item["state"] != "ready":
        raise Stop(f"reference/{name} cannot be adopted: {item['why']}. Nothing was written.",
                   409, code="source-refused")
    if item["sha256"] != source:
        raise Stop(f"reference/{name} changed since this preview read it. Nothing was "
                   "written. Read the preview again.", 409, stale=True, code="source-drift")
    claims = _claims(reading, name)
    for role in ("catalogue", "explanation"):
        if len(claims[role]) > 1:
            raise Stop(f"more than one entry claims reference/{name} as its {role}; settle "
                       "them by hand. Nothing was written.", 409, code="conflict")
    catalogue = claims["catalogue"][0] if claims["catalogue"] else None
    explanation = claims["explanation"][0] if claims["explanation"] else None
    commit = _commit_of(name)
    if op == "refresh":
        target = next((e for e in (catalogue, explanation)
                       if e and e["revision"] == expected), None)
        if target is None:
            raise Stop("the adopted entry changed since this preview read it. Nothing was "
                       "written. Read the preview again.", 409, stale=True,
                       code="destination-changed")
        if target["removed"] is not None:
            raise Stop(f"{target['id']} is retired; a tombstone is kept as it is.", 409,
                       code="retired")
        if target["adopted"]["sha256"] == source:
            raise Stop(f"{target['id']} already records these bytes. Nothing was written.",
                       409, code="unchanged")
        return {"item": item, "entry": target, "id": target["id"], "file": target["file"],
                "expected": target["revision"], "commit": commit}
    destination = entry_id(op, name)
    if expected != "none":
        raise Stop("a new adoption is held to there being no entry yet", 409, stale=True,
                   code="destination-changed")
    existing = catalogue if op == "catalogue" else explanation
    taken = [r for r in reading["scan"]["records"] if r.get("id") == destination]
    if existing is not None or taken or (brain.directory() / f"{destination}.md").exists():
        held = existing["id"] if existing else destination
        raise Stop(f"reference/{name} is already adopted as {held}. Nothing was written; "
                   "a human entry is never overwritten.", 409, stale=True,
                   code="already-adopted", existing=held)
    if op == "explanation":
        if catalogue is None:
            raise Stop("catalogue this file first: an explanation names the retained source "
                       "it came from. Nothing was written.", 409, code="catalogue-first")
        if catalogue["removed"] is not None:
            raise Stop(f"{catalogue['id']} is retired, so it cannot stand as this "
                       "explanation's source. Nothing was written.", 409, code="retired")
        if catalogue["adopted"]["sha256"] != source:
            raise Stop("the catalogue entry records different bytes; record the new bytes "
                       "first. Nothing was written.", 409, stale=True, code="catalogue-stale")
    return {"item": item, "entry": None, "id": destination, "file": f"{destination}.md",
            "expected": None, "commit": commit,
            "catalogue": catalogue["id"] if catalogue else None}


__all__ = ["Stop", "NotAName", "inventory", "preview", "about", "freeze", "entry_id",
           "field", "address", "catalogue_body", "OPERATIONS", "EXPLANATION_KINDS"]
