"""The page's assets come from one manifest and one boot snapshot (task 123).

`manager/core/web/manifest.json` lists every asset with its sha256, size
and the one type its suffix is served as; the build id is a digest of the
manifest, which does not list itself. A board reads the page and every
listed asset at start, verifies them, refuses to start when one is
missing or changed, and from then on serves that snapshot under
`/web/<build>/` — so a file changed on disk after boot is not served.
`BOARD_ASSETS=disk` is the developer's mode.

    python3 -m unittest tests.test_web_assets -v
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
WEB = CORE / "web"
sys.path.insert(0, str(CORE))

import assets  # noqa: E402
import config  # noqa: E402

from tests.test_settings_security import Board  # noqa: E402

PAGE = b"<!doctype html><link rel=stylesheet href=/web/__BUILD__/a.css>"


def entry(data: bytes, kind: str = "text/css; charset=utf-8") -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "type": kind}


def make_core(case: unittest.TestCase, files: dict, *, listed: dict | None = None,
              page: bytes = PAGE) -> Path:
    """A disposable core: board.html, web/<files>, and a manifest listing
    `listed` (default: every file, correctly)."""
    tmp = Path(tempfile.mkdtemp(prefix="bench-assets-")).resolve()
    case.addCleanup(shutil.rmtree, tmp, True)
    core = tmp / "core"
    web = core / "web"
    web.mkdir(parents=True)
    core.joinpath("board.html").write_bytes(page)
    for name, data in files.items():
        target = web / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if listed is None:
        listed = {name: entry(data) for name, data in files.items()}
    (web / "manifest.json").write_text(
        json.dumps({"v": 1, "assets": listed}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    return core


def refused(case: unittest.TestCase, core: Path, **kw) -> str:
    with case.assertRaises(assets.Refused) as caught:
        assets.load(core, **kw)
    return "; ".join(caught.exception.problems)


class TheShippedManifest(unittest.TestCase):
    def test_it_is_current_and_lists_every_asset(self):
        """What `assets.py --write` would write is what is committed, and
        nothing under web/ is outside it: every asset is in the manifest."""
        raw = (WEB / "manifest.json").read_bytes()
        self.assertEqual(assets.render(WEB), raw,
                         "web/manifest.json is stale: python3 "
                         "manager/core/assets.py --write")
        self.assertEqual(assets.unlisted(WEB, json.loads(raw)), [])

    def test_it_is_deterministic_and_not_self_referential(self):
        first, second = assets.render(WEB), assets.render(WEB)
        self.assertEqual(first, second)
        doc = json.loads(first)
        self.assertNotIn("manifest.json", doc["assets"])
        self.assertEqual(assets.build_id(first),
                         hashlib.sha256(first).hexdigest()[:16])
        self.assertIn("board.css", doc["assets"])

    def test_the_shipped_set_loads(self):
        snapshot = assets.load(CORE)
        self.assertEqual(snapshot.build,
                         assets.build_id((WEB / "manifest.json").read_bytes()))
        self.assertIn(assets.PLACEHOLDER, snapshot.html)

    def test_no_asset_names_an_absolute_url_it_would_fetch(self):
        """The stylesheets are the page's own: they reference nothing off
        the board — the Plex faces are web/fonts/, with their licence."""
        for name in json.loads((WEB / "manifest.json").read_bytes())["assets"]:
            text = (WEB / name).read_bytes()
            if name.endswith((".css", ".js", ".mjs")):
                self.assertNotIn(b"url(http", text, name)
                self.assertNotIn(b"@import", text, name)


class WhatAPathMayBe(unittest.TestCase):
    def test_normalized_relative_paths_with_a_typed_suffix(self):
        for good in ("board.css", "fonts/IBMPlexSans-Regular.woff2",
                     "fonts/README.md", "app/state.js", "IBMPlex.LICENSE.txt",
                     "fonts/-F63fjptAgt5VM-kVkqdyU8n1i8q131nj-o.woff2", "_x.css"):
            self.assertIsNone(assets.path_problem(good), good)

    def test_everything_else_is_refused(self):
        for bad in ("", "/board.css", "../board.css", "a/../board.css",
                    "./board.css", ".hidden.css", "a//b.css", "a\\b.css",
                    "fonts/", "board.exe", "board", "board.html",
                    "manifest.json", "a b.css", "board.css?x", "%2e%2e/x.css",
                    None, 7):
            self.assertIsNotNone(assets.path_problem(bad), repr(bad))

    def test_each_suffix_has_exactly_one_type(self):
        self.assertEqual(assets.TYPES[".css"], "text/css; charset=utf-8")
        self.assertEqual(assets.TYPES[".woff2"], "font/woff2")
        self.assertNotIn(".html", assets.TYPES)
        self.assertNotIn(".svg", assets.TYPES)


class TheManifestIsHeldToItself(unittest.TestCase):
    def test_a_good_core_loads(self):
        core = make_core(self, {"a.css": b"x{}", "fonts/f.woff2": b"wOF2...."},
                         listed={"a.css": entry(b"x{}"),
                                 "fonts/f.woff2": entry(b"wOF2....", "font/woff2")})
        snapshot = assets.load(core)
        self.assertEqual(snapshot.files["a.css"],
                         (b"x{}", "text/css; charset=utf-8"))
        self.assertEqual(snapshot.html, PAGE)

    def test_a_type_its_suffix_is_not_served_as_is_refused(self):
        core = make_core(self, {"a.css": b"x{}"},
                         listed={"a.css": entry(b"x{}", "text/javascript; charset=utf-8")})
        self.assertIn("type", refused(self, core))

    def test_a_hash_mismatch_is_refused(self):
        core = make_core(self, {"a.css": b"x{}"},
                         listed={"a.css": entry(b"y{}")})
        self.assertIn("does not match the manifest", refused(self, core))

    def test_a_missing_asset_is_refused(self):
        core = make_core(self, {}, listed={"a.css": entry(b"x{}")})
        self.assertIn("web/a.css is missing", refused(self, core))

    def test_a_missing_manifest_is_refused(self):
        core = make_core(self, {"a.css": b"x{}"})
        (core / "web" / "manifest.json").unlink()
        self.assertIn("manifest.json is missing", refused(self, core))

    def test_a_manifest_that_does_not_parse_is_refused(self):
        core = make_core(self, {"a.css": b"x{}"})
        (core / "web" / "manifest.json").write_text("{", encoding="utf-8")
        self.assertIn("does not parse", refused(self, core))

    def test_a_traversal_path_is_refused_before_anything_is_read(self):
        core = make_core(self, {"a.css": b"x{}"},
                         listed={"../board.css": entry(b"x{}")})
        self.assertIn("not a normalized relative path", refused(self, core))

    def test_a_link_inside_web_is_refused(self):
        core = make_core(self, {"a.css": b"x{}"})
        outside = core.parent / "outside.css"
        outside.write_bytes(b"x{}")
        (core / "web" / "a.css").unlink()
        (core / "web" / "a.css").symlink_to(outside)
        self.assertIn("link", refused(self, core))

    def test_a_linked_directory_escaping_web_is_refused(self):
        core = make_core(self, {"a.css": b"x{}"},
                         listed={"fonts/f.woff2": entry(b"wOF2", "font/woff2")})
        elsewhere = core.parent / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "f.woff2").write_bytes(b"wOF2")
        (core / "web" / "fonts").symlink_to(elsewhere, target_is_directory=True)
        self.assertIn("link", refused(self, core))

    def test_an_oversized_asset_is_refused(self):
        core = make_core(self, {"a.css": b"x" * 64})
        with mock.patch.object(assets, "MAX_FILE", 16):
            self.assertIn("limit", refused(self, core))

    def test_an_unknown_key_or_entry_shape_is_refused(self):
        core = make_core(self, {"a.css": b"x{}"},
                         listed={"a.css": {**entry(b"x{}"), "extra": 1}})
        self.assertIn("exactly sha256, bytes and type", refused(self, core))

    def test_a_page_that_names_no_build_is_refused(self):
        core = make_core(self, {"a.css": b"x{}"}, page=b"<!doctype html>")
        self.assertIn("names no build", refused(self, core))

    def test_every_problem_is_named_not_only_the_first(self):
        core = make_core(self, {"a.css": b"x{}"},
                         listed={"a.css": entry(b"y{}"), "b.css": entry(b"z")})
        problems = refused(self, core)
        self.assertIn("web/a.css does not match", problems)
        self.assertIn("web/b.css is missing", problems)

    def test_render_refuses_a_file_it_cannot_type(self):
        core = make_core(self, {"a.css": b"x{}"})
        (core / "web" / "notes.exe").write_bytes(b"MZ")
        with self.assertRaises(assets.Refused):
            assets.render(core / "web")
        self.assertEqual(assets.unlisted(core / "web",
                                         {"assets": {"a.css": {}}}),
                         ["notes.exe"])


class _Patched(unittest.TestCase):
    """In-process: `config.CORE` and `config.ASSETS_MODE` pointed at a
    disposable core, and the boot state restored afterwards."""

    def use(self, core: Path, mode: str = "snapshot") -> None:
        for patcher in (mock.patch.object(config, "CORE", core),
                        mock.patch.object(config, "ASSETS_MODE", mode),
                        mock.patch.object(assets, "_BOOTED", None),
                        mock.patch.object(assets, "_BOOTED_MODE", None)):
            patcher.start()
            self.addCleanup(patcher.stop)


class TheSnapshotIsWhatIsServed(_Patched):
    def test_a_file_changed_on_disk_after_boot_is_not_served(self):
        core = make_core(self, {"a.css": b"old{}"})
        self.use(core)
        build = assets.boot().build
        (core / "web" / "a.css").write_bytes(b"new{}")
        core.joinpath("board.html").write_bytes(b"<p>a newer page</p>")
        served = assets.lookup(build, "a.css")
        self.assertEqual(served.body, b"old{}")
        self.assertEqual(served.cache, assets.IMMUTABLE)
        self.assertEqual(served.type, "text/css; charset=utf-8")
        self.assertEqual(assets.page_html(), PAGE)
        (core / "web" / "a.css").unlink()
        self.assertEqual(assets.lookup(build, "a.css").body, b"old{}")

    def test_only_the_booted_build_and_listed_paths_are_served(self):
        core = make_core(self, {"a.css": b"x{}"})
        self.use(core)
        build = assets.boot().build
        (core / "web" / "dropped.css").write_bytes(b"y{}")
        self.assertIsNone(assets.lookup("0" * 16, "a.css"))
        self.assertIsNone(assets.lookup(assets.DISK_BUILD, "a.css"))
        self.assertIsNone(assets.lookup(build, "dropped.css"))
        self.assertIsNone(assets.lookup(build, "manifest.json"))
        self.assertIsNone(assets.lookup(build, "../board.html"))

    def test_nothing_is_served_before_a_boot(self):
        core = make_core(self, {"a.css": b"x{}"})
        self.use(core)
        build = assets.disk_build()
        self.assertIsNone(assets.lookup(build, "a.css"))
        self.assertEqual(assets.current_build(), build)

    def test_an_update_on_disk_is_reported_not_served(self):
        core = make_core(self, {"a.css": b"x{}"})
        self.use(core)
        build = assets.boot().build
        self.assertFalse(assets.public()["differs"])
        (core / "web" / "a.css").write_bytes(b"newer{}")
        (core / "web" / "manifest.json").write_bytes(assets.render(core / "web"))
        found = assets.public()
        self.assertTrue(found["differs"])
        self.assertEqual(found["build"], build)
        self.assertNotEqual(found["disk"], build)
        self.assertEqual(assets.lookup(build, "a.css").body, b"x{}")


class DiskModeIsForEditing(_Patched):
    def test_disk_mode_reads_every_request_uncached(self):
        core = make_core(self, {"a.css": b"x{}"})
        self.use(core, "disk")
        assets.boot()
        self.assertEqual(assets.current_build(), assets.DISK_BUILD)
        (core / "web" / "a.css").write_bytes(b"edited{}")
        served = assets.lookup(assets.DISK_BUILD, "a.css")
        self.assertEqual(served.body, b"edited{}")
        self.assertEqual(served.cache, assets.NO_STORE)
        core.joinpath("board.html").write_bytes(b"<p>edited page</p>")
        self.assertEqual(assets.page_html(), b"<p>edited page</p>")
        self.assertFalse(assets.public()["differs"])

    def test_disk_mode_still_serves_only_listed_regular_files(self):
        core = make_core(self, {"a.css": b"x{}"})
        self.use(core, "disk")
        assets.boot()
        (core / "web" / "dropped.css").write_bytes(b"y{}")
        self.assertIsNone(assets.lookup(assets.DISK_BUILD, "dropped.css"))
        self.assertIsNone(assets.lookup(assets.DISK_BUILD, "../board.html"))
        outside = core.parent / "o.css"
        outside.write_bytes(b"z")
        (core / "web" / "a.css").unlink()
        (core / "web" / "a.css").symlink_to(outside)
        self.assertIsNone(assets.lookup(assets.DISK_BUILD, "a.css"))

    def test_disk_mode_boot_skips_hashes_but_not_presence(self):
        core = make_core(self, {"a.css": b"x{}"}, listed={"a.css": entry(b"other")})
        self.use(core, "disk")
        assets.boot()
        core2 = make_core(self, {}, listed={"a.css": entry(b"x{}")})
        self.use(core2, "disk")
        with self.assertRaises(assets.Refused):
            assets.boot()


class TheSetting(unittest.TestCase):
    def test_two_words_and_a_named_fallback(self):
        self.assertEqual(config.parse_assets(""), ("snapshot", None))
        self.assertEqual(config.parse_assets("Disk"), ("disk", None))
        mode, problem = config.parse_assets("off")
        self.assertEqual(mode, "snapshot")
        self.assertIn("BOARD_ASSETS='off'", problem)

    def test_it_is_documented(self):
        example = (CORE / ".env.example").read_text(encoding="utf-8")
        self.assertIn("\nBOARD_ASSETS=snapshot\n", example)


def fetch(board: Board, path: str) -> tuple[int, dict, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", board.port, timeout=20)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def build_of(page: bytes) -> str:
    marker = b'<meta name="bench-build" content="'
    start = page.index(marker) + len(marker)
    return page[start:page.index(b'"', start)].decode("ascii")


class ARunningBoard(unittest.TestCase):
    def test_the_page_names_its_build_and_the_build_is_served_immutable(self):
        board = Board(self)
        status, headers, page = fetch(board, "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        build = build_of(page)
        self.assertNotIn(b"__BUILD__", page)
        self.assertIn(f'href="/web/{build}/board.css"'.encode(), page)
        status, headers, css = fetch(board, f"/web/{build}/board.css")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/css; charset=utf-8")
        self.assertEqual(headers["cache-control"], assets.IMMUTABLE)
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(css, (WEB / "board.css").read_bytes())
        # the font stylesheet and every face it names, from the same build
        self.assertIn(f'href="/web/{build}/fonts/plex.css"'.encode(), page)
        status, headers, fonts = fetch(board, f"/web/{build}/fonts/plex.css")
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], assets.IMMUTABLE)
        for name in sorted(set(re.findall(rb"url\(([^)]+)\)", fonts))):
            status, headers, body = fetch(board, f"/web/{build}/fonts/{name.decode()}")
            self.assertEqual(status, 200, name)
            self.assertEqual(headers["content-type"], "font/woff2")
            self.assertEqual(headers["cache-control"], assets.IMMUTABLE)
            self.assertEqual(body[:4], b"wOF2")
        code, state = board.get("/api/state")
        self.assertEqual(state["assets"]["build"], build)
        self.assertEqual(state["assets"]["mode"], "snapshot")
        self.assertFalse(state["assets"]["differs"])
        for path in (f"/web/{'0' * 16}/board.css", f"/web/{build}/manifest.json",
                     f"/web/{build}/../board.html", f"/web/{build}/",
                     f"/web/{build}/%2e%2e/board.py", "/web/disk/board.css",
                     f"/web/{build}/nothing.css"):
            self.assertEqual(fetch(board, path)[0], 404, path)

    def test_a_file_changed_on_disk_after_boot_is_not_served(self):
        board = Board(self)
        build = build_of(fetch(board, "/")[2])
        web = board.tool / "manager" / "core" / "web"
        (web / "board.css").write_bytes(b"/* rewritten after boot */")
        (web / "manifest.json").write_bytes(assets.render(web))
        status, _, css = fetch(board, f"/web/{build}/board.css")
        self.assertEqual(status, 200)
        self.assertEqual(css, (WEB / "board.css").read_bytes())
        self.assertEqual(build_of(fetch(board, "/")[2]), build)
        _, state = board.get("/api/state")
        self.assertTrue(state["assets"]["differs"])
        self.assertEqual(state["assets"]["disk"], assets.build_id(
            (web / "manifest.json").read_bytes()))

    def _refused_start(self, prepare) -> str:
        with self.assertRaises(AssertionError) as caught:
            Board(self, prepare=prepare)
        return str(caught.exception)

    def test_a_missing_asset_refuses_the_start(self):
        def prepare(board):
            (board.tool / "manager" / "core" / "web" / "board.css").unlink()
        why = self._refused_start(prepare)
        self.assertIn("cannot serve its page's assets", why)
        self.assertIn("web/board.css is missing", why)

    def test_a_changed_asset_refuses_the_start(self):
        def prepare(board):
            (board.tool / "manager" / "core" / "web" / "board.css").write_bytes(
                b"/* half an update */")
        why = self._refused_start(prepare)
        self.assertIn("does not match the manifest", why)

    def test_disk_mode_serves_edits_uncached(self):
        board = Board(self, env={"BOARD_ASSETS": "disk"})
        page = fetch(board, "/")[2]
        self.assertEqual(build_of(page), "disk")
        web = board.tool / "manager" / "core" / "web"
        (web / "board.css").write_bytes(b"/* an edit */")
        status, headers, css = fetch(board, "/web/disk/board.css")
        self.assertEqual(status, 200)
        self.assertEqual(css, b"/* an edit */")
        self.assertEqual(headers["cache-control"], "no-store")


class TheArtifactCarriesTheAssets(unittest.TestCase):
    """release.sh ships manager/core as a tree, so the manifest and every
    asset it lists reach a release install — and load there."""

    def test_an_unpacked_release_loads_its_snapshot(self):
        scratch = Path(tempfile.mkdtemp(prefix="bench-assets-artifact-")).resolve()
        self.addCleanup(shutil.rmtree, scratch, True)
        tarball = scratch / "bench.tar.gz"
        result = subprocess.run(
            ["bash", str(REPO / "release.sh"), "--tarball", str(tarball),
             "--source", "example/bench"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        unpacked = scratch / "tm"
        unpacked.mkdir()
        with tarfile.open(tarball) as tar:
            names = {m.name.removeprefix("./") for m in tar.getmembers()}
            tar.extractall(unpacked)
        listed = json.loads((WEB / "manifest.json").read_bytes())["assets"]
        self.assertIn("manager/core/web/manifest.json", names)
        for name in listed:
            self.assertIn(f"manager/core/web/{name}", names)
        snapshot = assets.load(unpacked / "manager" / "core")
        self.assertEqual(snapshot.build, assets.build_id(
            (WEB / "manifest.json").read_bytes()))
        # the fonts and their licence travel in the artifact (WP14)
        fonts = [n for n in listed if n.startswith("fonts/") and n.endswith(".woff2")]
        self.assertEqual(len(fonts), 24)
        for name in fonts:
            self.assertEqual(snapshot.files[name][0][:4], b"wOF2", name)
        for name in ("fonts/plex.css", "fonts/IBMPlex.LICENSE.txt",
                     "fonts/SOURCES.txt", "fonts/README.md"):
            self.assertIn(name, snapshot.files)
        self.assertIn(b"SIL Open Font License",
                      snapshot.files["fonts/IBMPlex.LICENSE.txt"][0])


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("BOARD_", "BENCH_"))}


class ThreeLayoutsBootAndUpdate(_Patched):
    """The same asset behaviour in each layout an install can take —
    vendored (the release unpacked into `.task-manager/` of a host
    repository), source (a git checkout of bench itself, run in place) and
    external (the tool in its own directory with `--app` and `--work`):

    - the installed core loads its snapshot, fonts and licence included,
      under the build the release names;
    - a board booted there keeps serving the booted bytes, page and fonts
      alike, after an update rewrites `manager/core/` — `rsync -a
      --delete`, as update.sh does, for the two release layouts, and the
      new files written in place, as a pull does, for the checkout — and
      reports the build on disk as differing rather than serving it;
    - the updated core then loads as the newer build on its own.
    """

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-assets-layouts-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = subprocess.run(
            ["bash", str(REPO / "release.sh"), "--tarball", str(cls.tarball),
             "--source", "example/bench"],
            capture_output=True, text=True)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(f"release.sh failed:\n{result.stdout}{result.stderr}")
        cls.build = assets.build_id((WEB / "manifest.json").read_bytes())
        # The "next release": the same artifact with a changed stylesheet
        # and a font file of a different subset's bytes, manifest rewritten.
        cls.next = cls.scratch / "next"
        cls.next.mkdir()
        subprocess.run(["tar", "-xzf", str(cls.tarball), "-C", str(cls.next)],
                       check=True, capture_output=True)
        web = cls.next / "manager" / "core" / "web"
        with open(web / "board.css", "ab") as css:
            css.write(b"\n/* the next release */\n")
        cls.font = "fonts/zYXzKVElMYYaJe8bpLHnCwDKr932-G7dytD-Dmu1syxeKYbSB4Zh.woff2"
        (web / cls.font).write_bytes((web / "fonts" /
            "zYXzKVElMYYaJe8bpLHnCwDKr932-G7dytD-Dmu1syxQKYbSB4ZhRNU.woff2").read_bytes())
        (web / "manifest.json").write_bytes(assets.render(web))
        cls.next_build = assets.build_id((web / "manifest.json").read_bytes())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def unpack(self, into: Path) -> None:
        into.mkdir(parents=True)
        subprocess.run(["tar", "-xzf", str(self.tarball), "-C", str(into)],
                       check=True, capture_output=True)

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], check=True, capture_output=True,
                       env={**_clean_env(), "GIT_AUTHOR_NAME": "t",
                            "GIT_AUTHOR_EMAIL": "t@example.invalid",
                            "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@example.invalid"})

    def vendored(self, tmp: Path) -> Path:
        host = tmp / "host"
        self.git("init", "-q", "-b", "main", str(host))
        (host / ".claude").mkdir()
        tm = host / ".task-manager"
        self.unpack(tm)
        # As test_update_from_release does, the adapter wiring's own exit is
        # not this test's subject; the work state the installer lays down
        # before wiring is.
        result = subprocess.run([sys.executable, str(tm / "install.py")], cwd=host,
                                capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, env=_clean_env())
        self.assertTrue((tm / "tasks" / "backlog").is_dir(),
                        result.stdout + result.stderr)
        return tm / "manager" / "core"

    def source(self, tmp: Path) -> Path:
        """A checkout of bench: the release's tree committed as a repository
        and run from where it sits, with no install step of its own."""
        checkout = tmp / "bench"
        self.unpack(checkout)
        self.git("init", "-q", "-b", "main", str(checkout))
        self.git("-C", str(checkout), "add", "-A")
        self.git("-C", str(checkout), "commit", "-q", "-m", "bench")
        return checkout / "manager" / "core"

    def external(self, tmp: Path) -> Path:
        tool, app, work = tmp / "tool", tmp / "app", tmp / "work"
        self.unpack(tool)
        self.git("init", "-q", "-b", "main", str(app))
        result = subprocess.run(
            [sys.executable, str(tool / "install.py"), "--app", str(app),
             "--work", str(work)],
            capture_output=True, text=True, cwd=str(tmp), env=_clean_env(),
            stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((work / "tasks").is_dir())
        return tool / "manager" / "core"

    def check(self, layout: str) -> None:
        tmp = Path(tempfile.mkdtemp(prefix=f"{layout}-", dir=self.scratch))
        core = getattr(self, layout)(tmp)
        self.use(core)
        booted = assets.boot()
        self.assertEqual(booted.build, self.build, layout)
        page = assets.page_html()
        css = assets.lookup(self.build, "board.css").body
        font = assets.lookup(self.build, self.font)
        self.assertEqual(font.body[:4], b"wOF2")
        self.assertEqual(font.type, "font/woff2")
        self.assertIsNotNone(assets.lookup(self.build, "fonts/IBMPlex.LICENSE.txt"))

        if layout == "source":
            # a pull writes the newer files over the checkout
            for path in (self.next / "manager" / "core").rglob("*"):
                if path.is_file():
                    target = core / path.relative_to(self.next / "manager" / "core")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(path.read_bytes())
        else:
            subprocess.run(["rsync", "-a", "--delete",
                            f"{self.next}/manager/core/", f"{core}/"],
                           check=True, capture_output=True)

        self.assertEqual(assets.page_html(), page, layout)
        self.assertEqual(assets.lookup(self.build, "board.css").body, css, layout)
        self.assertEqual(assets.lookup(self.build, self.font).body, font.body, layout)
        self.assertIsNone(assets.lookup(self.next_build, "board.css"),
                          "a running board serves only the build it booted")
        public = assets.public()
        self.assertTrue(public["differs"], layout)
        self.assertEqual(public["build"], self.build)
        self.assertEqual(public["disk"], self.next_build)
        fresh = assets.load(core)
        self.assertEqual(fresh.build, self.next_build, layout)
        self.assertTrue(fresh.files["board.css"][0].endswith(b"/* the next release */\n"))

    def test_vendored(self):
        self.check("vendored")

    def test_source(self):
        self.check("source")

    def test_external(self):
        self.check("external")


if __name__ == "__main__":
    unittest.main()
