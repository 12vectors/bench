"""The page's static assets: one manifest, one build id, one boot snapshot.

Everything the board's page loads besides the page itself lives under
`manager/core/web/` and is listed in `web/manifest.json` with its
sha256, its size and the one MIME type its suffix may be served as. The
manifest does not list itself, and the **build id** is a digest of its
bytes, so the id names exactly one set of asset contents and nothing
that depends on the id (the page, which names it) is inside it.

A running board serves the set it booted with (task 123, WP14 of the
maintainability plan). At start `board.py` asks `boot()`, which reads
`board.html` and every listed asset into memory, verifies each hash, and
refuses — the board does not serve — when the manifest does not parse, a
path is not a normalized relative path, a suffix is not one this module
types, a file is missing, is not a regular file, is reached through a
link, is too large, or does not match its hash. From then on assets are
served from memory under `/web/<build>/<path>`, immutable and cacheable,
and an update that rewrites `manager/core/` on disk — `update.sh`'s
`rsync --delete` is not atomic — cannot produce a page mixing two builds:
a file changed after boot is simply not what is served. `board.html`
itself stays `no-store` and names the build id.

`BOARD_ASSETS=disk` is the developer's mode: the page and its assets are
read from disk on every request, served `no-store` under the build id
`disk`, and hashes are not checked, so an edit shows on reload. Paths,
suffixes and links are held to the same rules in both modes, and only
files the manifest lists are ever served: this is not a directory
listing, and nothing a person or agent writes elsewhere reaches it.

What this does not do, said plainly: it snapshots the page's *assets*.
Python modules are a separate question, answered by `board.py` importing
every core module at startup (task 122); separate processes started
after an update — the shims, the adapters' `run` — are not covered by
either and may talk to an older running board.

Stdlib only. Imports `config`, for where core is and which mode is in
force.

    python3 manager/core/assets.py            # is the manifest current?
    python3 manager/core/assets.py --write    # rewrite it from web/
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import config

MANIFEST = "manifest.json"
VERSION = 1

# The enumerated suffix map: the only files the asset route serves, and the
# one type each is served as. A manifest naming another type for a suffix,
# or a suffix not here, is refused — so a stylesheet can never be sent as
# script, nor a font as a page.
TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}

# Bounds, so a snapshot is the page's assets and never whatever someone
# dropped beside them: a cap per file, on the whole set, and on the count.
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FILES = 512
MAX_MANIFEST = 1024 * 1024

# Where board.html names its build. The server writes the id in its place.
PLACEHOLDER = b"__BUILD__"
# The build id disk mode serves under: not a digest, so a cached copy from
# a snapshot build is never mistaken for it.
DISK_BUILD = "disk"

IMMUTABLE = "public, max-age=31536000, immutable"
NO_STORE = "no-store"

# One path segment: starts with a letter or digit (so no `.`, `..` or
# hidden file), then letters, digits, `.`, `_`, `-`.
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_BUILD = re.compile(r"[0-9a-f]{16}\Z")


class Refused(Exception):
    """The asset set does not hold together, so the board does not serve
    it. `problems` names every one found, not only the first."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class Snapshot(NamedTuple):
    build: str
    files: dict          # path → (bytes, type)
    html: bytes
    manifest: bytes


class Served(NamedTuple):
    body: bytes
    type: str
    cache: str


def web_dir(core: Path | str | None = None) -> Path:
    return Path(core if core is not None else config.CORE) / "web"


def build_id(manifest: bytes) -> str:
    """The build id: the first 16 hex digits of the manifest's sha256."""
    return hashlib.sha256(manifest).hexdigest()[:16]


def mode() -> str:
    """`snapshot` (the default) or `disk` (BOARD_ASSETS=disk)."""
    return config.ASSETS_MODE


# ── what a path may be ──────────────────────────────────────────────────


def path_problem(name) -> str | None:
    """Why `name` is not an asset path, or None. A normalized relative
    POSIX path of plain segments with a suffix this module types; never
    the manifest itself."""
    if not isinstance(name, str) or not name:
        return f"{name!r} is not an asset path"
    if len(name) > 200:
        return f"{name[:40]!r}… is longer than an asset path may be"
    for part in name.split("/"):
        if not _SEGMENT.match(part):
            return (f"{name!r} is not a normalized relative path: every "
                    f"segment must start with a letter or digit and hold "
                    f"only letters, digits, '.', '_' and '-'")
    if name == MANIFEST:
        return "the manifest does not list itself"
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in TYPES:
        return (f"{name!r} has the suffix {suffix or '(none)'!r}, which is "
                f"not one of {', '.join(sorted(TYPES))}")
    return None


def _open_beneath(web: Path, name: str, cap: int) -> bytes:
    """The bytes of `web/name`, refusing a missing file, any segment that
    is a link, anything but a regular file, a path resolving outside
    `web`, and a file over `cap`. Raises Refused with one problem."""
    current = web
    info = None
    for part in name.split("/"):
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            raise Refused([f"web/{name} is missing"]) from None
        except OSError as exc:
            raise Refused([f"web/{name} cannot be read: {exc.strerror}"]) from None
        if stat.S_ISLNK(info.st_mode):
            raise Refused([f"web/{name} is reached through a link; assets "
                           f"are served only as regular files inside web/"])
    if info is None or not stat.S_ISREG(info.st_mode):
        raise Refused([f"web/{name} is not a regular file"])
    try:
        inside = current.resolve().is_relative_to(web.resolve())
    except OSError:
        inside = False
    if not inside:
        raise Refused([f"web/{name} resolves outside web/"])
    if info.st_size > cap:
        raise Refused([f"web/{name} is {info.st_size} bytes, over the "
                       f"{cap}-byte limit"])
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(current, flags)
    except OSError as exc:
        raise Refused([f"web/{name} cannot be read: {exc.strerror}"]) from None
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise Refused([f"web/{name} grew past the {cap}-byte limit"])
    return data


# ── the manifest ────────────────────────────────────────────────────────


def read_manifest(web: Path) -> tuple[bytes, dict]:
    """The manifest's bytes and its validated document. Raises Refused
    naming every structural problem."""
    raw = _open_beneath(web, MANIFEST, MAX_MANIFEST)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Refused([f"web/{MANIFEST} does not parse: {exc}"]) from None
    if not isinstance(doc, dict) or doc.get("v") != VERSION:
        raise Refused([f"web/{MANIFEST} is not a version-{VERSION} manifest"])
    listed = doc.get("assets")
    if not isinstance(listed, dict):
        raise Refused([f"web/{MANIFEST} has no \"assets\" object"])
    problems = []
    unknown = sorted(set(doc) - {"v", "assets"})
    if unknown:
        problems.append(f"web/{MANIFEST} has keys it may not: {unknown}")
    if len(listed) > MAX_FILES:
        problems.append(f"web/{MANIFEST} lists {len(listed)} assets, over "
                        f"the limit of {MAX_FILES}")
    total = 0
    for name, entry in sorted(listed.items()):
        why = path_problem(name)
        if why:
            problems.append(why)
            continue
        if not isinstance(entry, dict) or set(entry) != {"sha256", "bytes", "type"}:
            problems.append(f"{name!r}: an entry is exactly sha256, bytes "
                            f"and type")
            continue
        if not isinstance(entry["sha256"], str) or not _HEX64.match(entry["sha256"]):
            problems.append(f"{name!r}: sha256 is not 64 lowercase hex digits")
        size = entry["bytes"]
        if type(size) is not int or size < 0 or size > MAX_FILE:
            problems.append(f"{name!r}: bytes is not a size within the "
                            f"{MAX_FILE}-byte limit")
        else:
            total += size
        expected = TYPES[PurePosixPath(name).suffix.lower()]
        if entry["type"] != expected:
            problems.append(f"{name!r}: type {entry['type']!r} is not the "
                            f"{expected!r} its suffix is served as")
    if total > MAX_TOTAL:
        problems.append(f"the assets total {total} bytes, over the "
                        f"{MAX_TOTAL}-byte limit")
    if problems:
        raise Refused(problems)
    return raw, doc


def tree(web: Path) -> list[str]:
    """Every file under `web/` but the manifest, as relative POSIX paths,
    sorted — what a manifest written now would have to list."""
    found = []
    if not web.is_dir():
        return found
    for path in web.rglob("*"):
        if path.name == "__pycache__" or "__pycache__" in path.parts:
            continue
        if path.is_file() or path.is_symlink():
            rel = path.relative_to(web).as_posix()
            if rel != MANIFEST:
                found.append(rel)
    return sorted(found)


def render(web: Path) -> bytes:
    """The manifest `web/` should carry now, deterministically: sorted
    keys, one-space indent, a final newline. Raises Refused for a file
    that could not be listed (a bad path, an unknown suffix, a link)."""
    assets, problems = {}, []
    for name in tree(web):
        why = path_problem(name)
        if why:
            problems.append(why)
            continue
        try:
            data = _open_beneath(web, name, MAX_FILE)
        except Refused as refused:
            problems += refused.problems
            continue
        assets[name] = {"sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data),
                        "type": TYPES[PurePosixPath(name).suffix.lower()]}
    if problems:
        raise Refused(problems)
    doc = {"v": VERSION, "assets": assets}
    return (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8")


def unlisted(web: Path, doc: dict) -> list[str]:
    """Files under web/ the manifest does not list."""
    return [name for name in tree(web) if name not in doc.get("assets", {})]


# ── the snapshot ────────────────────────────────────────────────────────


def load(core: Path | str | None = None, *, hashes: bool = True) -> Snapshot:
    """Read the page and every listed asset, verified. Raises Refused
    naming every problem. With `hashes=False` (disk mode's boot check)
    presence, paths, links and sizes are checked and contents are not."""
    core = Path(core if core is not None else config.CORE)
    web = web_dir(core)
    raw, doc = read_manifest(web)
    files, problems = {}, []
    for name, entry in sorted(doc["assets"].items()):
        try:
            data = _open_beneath(web, name, MAX_FILE)
        except Refused as refused:
            problems += refused.problems
            continue
        if hashes:
            digest = hashlib.sha256(data).hexdigest()
            if digest != entry["sha256"] or len(data) != entry["bytes"]:
                problems.append(f"web/{name} does not match the manifest: "
                                f"sha256 {digest[:12]}…, {len(data)} bytes; "
                                f"the manifest says {entry['sha256'][:12]}…, "
                                f"{entry['bytes']} bytes")
                continue
        files[name] = (data, entry["type"])
    try:
        html = (core / "board.html").read_bytes()
    except OSError:
        html = b""
        problems.append("board.html is missing")
    if html and html.count(PLACEHOLDER) == 0 and doc["assets"]:
        problems.append(f"board.html names no build ({PLACEHOLDER.decode()}), "
                        f"so it could not load its assets")
    if problems:
        raise Refused(problems)
    return Snapshot(build_id(raw), files, html, raw)


_BOOTED: Snapshot | None = None
_BOOTED_MODE: str | None = None


def boot(core: Path | str | None = None) -> Snapshot:
    """Take the snapshot this board serves until it stops. Raises Refused,
    and then the board does not start. In disk mode the same checks run
    except the hashes, and nothing is kept: every request reads disk."""
    global _BOOTED, _BOOTED_MODE
    if mode() == "disk":
        snapshot = load(core, hashes=False)
        _BOOTED, _BOOTED_MODE = None, "disk"
        return snapshot
    snapshot = load(core)
    _BOOTED, _BOOTED_MODE = snapshot, "snapshot"
    return snapshot


def booted() -> Snapshot | None:
    return _BOOTED


def disk_build(core: Path | str | None = None) -> str | None:
    """The build id of the manifest on disk now, or None when it cannot be
    read. Cheap — the manifest only — so /api/state can ask it."""
    try:
        return build_id(_open_beneath(web_dir(core), MANIFEST, MAX_MANIFEST))
    except Refused:
        return None


def current_build() -> str:
    """The build the page names: the booted snapshot's, `disk` in disk
    mode, and before any boot (a test, a probe) the disk manifest's."""
    if mode() == "disk":
        return DISK_BUILD
    if _BOOTED is not None:
        return _BOOTED.build
    return disk_build() or "unbooted"


def page_html() -> bytes | None:
    """board.html as this board serves it: the snapshot's copy once booted
    in snapshot mode, else read from disk now (None when missing)."""
    if mode() != "disk" and _BOOTED is not None:
        return _BOOTED.html
    try:
        return (Path(config.CORE) / "board.html").read_bytes()
    except OSError:
        return None


def lookup(build: str, name: str) -> Served | None:
    """The asset `name` of `build`, or None (a 404). Only the build in
    force is served — an old page asking for its own build gets nothing
    rather than a mixed page — and only a listed, well-formed path."""
    if path_problem(name):
        return None
    if mode() == "disk":
        if build != DISK_BUILD:
            return None
        web = web_dir()
        try:
            _raw, doc = read_manifest(web)
            if name not in doc["assets"]:
                return None
            data = _open_beneath(web, name, MAX_FILE)
        except Refused:
            return None
        return Served(data, doc["assets"][name]["type"], NO_STORE)
    snapshot = _BOOTED
    if snapshot is None or build != snapshot.build or name not in snapshot.files:
        return None
    data, kind = snapshot.files[name]
    return Served(data, kind, IMMUTABLE)


def public() -> dict:
    """What /api/state says about the assets: the build being served, the
    mode, the build on disk now, and whether the two differ — which is an
    update waiting for a restart, never a reason to serve the new files."""
    served = current_build()
    on_disk = disk_build()
    differs = (mode() == "snapshot" and _BOOTED is not None
               and on_disk != _BOOTED.build)
    return {"build": served, "mode": mode(),
            "booted": _BOOTED_MODE is not None,
            "disk": on_disk, "differs": differs}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    web = web_dir()
    try:
        wanted = render(web)
    except Refused as refused:
        for problem in refused.problems:
            print(f"refused: {problem}", file=sys.stderr)
        return 2
    path = web / MANIFEST
    if argv == ["--write"]:
        path.write_bytes(wanted)
        print(f"wrote {path} — build {build_id(wanted)}")
        return 0
    if argv:
        print("usage: assets.py [--write]", file=sys.stderr)
        return 2
    try:
        current = path.read_bytes()
    except OSError:
        current = b""
    if current != wanted:
        print(f"{path} is not current; run with --write", file=sys.stderr)
        return 1
    print(f"{path} is current — build {build_id(wanted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
