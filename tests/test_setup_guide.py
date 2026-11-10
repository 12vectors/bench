"""The project setup guide and its shared state (task 88).

The guide reads six areas from the authorities that already answer for
them, and writes only two things: a person's decision about an area, and
a proposed setup card. Each test below is one promise the card makes:

- reading writes nothing and runs nothing, and an unconfigured project
  reads as decisions needed — never as tested;
- an existing configured project reads as configured, not tested, and
  nothing about it is reset;
- the browser's route and the CLI read the same state and meet the same
  refusals, through the same functions;
- a stale revision, a replayed request, an interrupted mutation, a newer
  document version and a hand-broken file each preserve the prior valid
  state and say how to recover;
- unknown fields survive a write, and the shared file carries no path and
  no credential.

    python3 -m unittest tests.test_setup_guide -v
"""

from __future__ import annotations

import json
import os
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
HEADERS = {"Content-Type": "application/json", "X-Bench-Settings": "1"}
STAGES = ("backlog", "to-do", "in-progress", "review", "done", "archive")

PRELUDE = (
    "import json, os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import config, setupguide\n"
)


def git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    out = subprocess.run(["git", "-C", str(repo), *args], env=env,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(f"fixture git {args}: {out.stderr}")


def snapshot(*roots: Path) -> dict:
    """Every file under the roots with its bytes, so "wrote nothing" is a
    comparison rather than a belief. `.git` is skipped: git itself may
    refresh its index on a status read, which is not a write of ours."""
    found = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if ".git" in path.relative_to(root).parts or "__pycache__" in path.parts:
                continue
            found[str(path)] = path.read_bytes() if path.is_file() else None
    return found


class Project:
    """A disposable external layout: the candidate's own core as the tool,
    an application and a work state of its own."""

    def __init__(self, case: unittest.TestCase, *, work_repo=True,
                 instructions=False, policy=None, driver=False):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-setup-"))
        case.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.tool = self.dir / "tool"
        self.app, self.work = self.dir / "app", self.dir / "work"
        shutil.copytree(CORE, self.tool / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.local = self.tool / "manager" / "local"
        self.local.mkdir(parents=True)
        (self.local / ".env").write_text("", encoding="utf-8")
        self.state = self.local / "state"
        self.app.mkdir()
        git(self.app, "init", "-q", "-b", "main")
        (self.app / "README.md").write_text("app\n")
        if instructions:
            (self.app / "AGENTS.md").write_text("# How we work\n")
        git(self.app, "add", "-A")
        git(self.app, "-c", "user.email=t@example.com", "-c",
            "user.name=tester", "commit", "-q", "-m", "fixture")
        self.work.mkdir()
        if work_repo:
            git(self.work, "init", "-q", "-b", "main")
            git(self.work, "config", "user.name", "tester")
            git(self.work, "config", "user.email", "t@example.com")
        for stage in STAGES:
            (self.work / "tasks" / stage).mkdir(parents=True)
        (self.work / "tasks" / "backlog" / "07-existing.md").write_text(
            "# 07 — Existing\n\n**Status:** Backlog\n**Priority:** Low\n")
        if policy is not None:
            (self.local / "profiles.json").write_text(json.dumps(policy))
        if driver:
            d = self.local / "driver"
            d.mkdir()
            (d / "start").write_text("#!/bin/sh\necho 'DRIVE URL: x'\n")
            (d / "start").chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("BOARD_", "AGENT_", "BENCH_"))}
        self.env.update({
            "BOARD_APP_ROOT": str(self.app), "BOARD_WORK_ROOT": str(self.work),
            "BOARD_WORKTREES": str(self.dir / "worktrees"),
            "BOARD_SYNC": "0", "BOARD_COMMIT_MOVES": "0",
            "BOARD_GH_BIN": str(self.dir / "no-gh"),
            "BOARD_CLAUDE_BIN": str(self.dir / "no-claude"),
            "BOARD_PR_POLL_INTERVAL": "3600", "BOARD_PHASE_INTERVAL": "3600",
            "BOARD_WATCH_INTERVAL": "3600"})

    @property
    def core(self) -> Path:
        return self.tool / "manager" / "core"

    def run(self, script: str, extra: dict | None = None):
        env = dict(self.env, **(extra or {}))
        out = subprocess.run(
            [sys.executable, "-c", PRELUDE + script + "\nprint(json.dumps(R))",
             str(self.core)], env=env, text=True, capture_output=True,
            cwd=str(self.dir))
        if out.returncode != 0:
            raise AssertionError(f"probe failed: {out.stderr[-3000:]}")
        return json.loads(out.stdout.splitlines()[-1])

    def read(self) -> dict:
        return self.run("R = setupguide.state()")

    def call(self, fn: str, payload: dict) -> dict:
        """One mutation, answered as an API would: the result, or the
        refusal's status and extra fields."""
        return self.run(
            f"try:\n    R = {{'ok': setupguide.{fn}({payload!r})}}\n"
            "except setupguide.Refused as e:\n"
            "    R = {'refused': str(e), 'status': e.status, **e.extra}")

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.core / "board.py"), "--setup-guide",
             *args], env=self.env, text=True, capture_output=True,
            cwd=str(self.dir))

    def decisions(self) -> dict:
        return json.loads((self.work / "setup" / "decisions.json").read_text())


def by_id(found: dict) -> dict:
    return {a["id"]: a for a in found["areas"]}


class ReadingAnUnconfiguredProject(unittest.TestCase):
    def test_it_reads_real_facts_as_decisions_and_never_as_tested(self):
        p = Project(self, work_repo=False)
        found = p.read()
        areas = by_id(found)
        self.assertEqual(["workspace", "agent", "instructions", "verification",
                          "preview", "delivery"], [a["id"] for a in found["areas"]])
        self.assertEqual("decision-needed", areas["workspace"]["status"])
        self.assertEqual("blocked", areas["agent"]["status"])
        self.assertEqual("you", areas["agent"]["next"]["owner"])
        self.assertEqual("decision-needed", areas["instructions"]["status"])
        self.assertEqual("agent", areas["instructions"]["next"]["owner"])
        self.assertEqual("decision-needed", areas["verification"]["status"])
        self.assertEqual("decision-needed", areas["preview"]["status"])
        self.assertEqual("decision-needed", areas["delivery"]["status"])
        for area in found["areas"]:
            self.assertNotEqual("tested", area["status"])
            self.assertTrue(area["recommendation"], area["id"])
            self.assertTrue(area["consequence"], area["id"])
            self.assertIn(area["next"]["owner"], ("you", "agent", "bench"))
        self.assertTrue(found["summary"]["invite"])
        self.assertEqual(sorted(found["statuses"]),
                         sorted(["not-inspected", "decision-needed",
                                 "configured", "tested", "failed", "blocked",
                                 "deferred", "not-applicable"]))
        self.assertNotIn("percent", json.dumps(found["summary"]["counts"]))
        self.assertEqual("none", found["revision"])

    def test_reading_writes_nothing_anywhere(self):
        p = Project(self)
        before = snapshot(p.tool, p.app, p.work)
        p.read()
        out = p.cli("--json")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(before, snapshot(p.tool, p.app, p.work))
        self.assertFalse((p.work / "setup").exists())
        self.assertFalse((p.state / "setup").exists())


class AnExistingConfiguredProject(unittest.TestCase):
    POLICY = {"v": 1,
              "checks": [{"name": "unit", "run": "python3 -m unittest",
                          "root": "worktree"}],
              "profiles": {"work": {"verify": ["unit"]}}}

    def test_configured_is_not_tested_and_nothing_is_reset(self):
        p = Project(self, instructions=True, policy=self.POLICY, driver=True)
        policy_before = (p.local / "profiles.json").read_text()
        areas = by_id(p.read())
        self.assertEqual("configured", areas["workspace"]["status"])
        self.assertEqual("configured", areas["instructions"]["status"])
        self.assertIn("AGENTS.md", areas["instructions"]["recommendation"])
        self.assertEqual("configured", areas["verification"]["status"])
        self.assertEqual("configured", areas["preview"]["status"])
        self.assertNotIn("create-task", areas["instructions"]["allowed"])
        self.assertEqual(policy_before, (p.local / "profiles.json").read_text())
        self.assertEqual("# How we work\n", (p.app / "AGENTS.md").read_text())

    def test_a_refused_policy_reads_blocked_with_settings_as_the_way(self):
        p = Project(self, policy={"v": 1, "checks": [
            {"name": "unit", "run": "python3 -m unittest", "root": "app"}]})
        area = by_id(p.read())["verification"]
        self.assertEqual("blocked", area["status"])
        self.assertEqual("settings", area["next"]["kind"])


class OneContractForBrowserAndCli(unittest.TestCase):
    def test_the_cli_json_is_the_state_the_function_returns(self):
        p = Project(self)
        direct = p.read()
        out = p.cli("--json")
        self.assertEqual(0, out.returncode, out.stderr)
        via_cli = json.loads(out.stdout)
        for found in (direct, via_cli):
            found.pop("generated")
        self.assertEqual(direct, via_cli)

    def test_the_cli_meets_the_same_refusal(self):
        p = Project(self)
        out = p.cli("--decide", "workspace", "not-applicable", "--reason",
                    "no thanks")
        self.assertEqual(1, out.returncode)
        self.assertIn("cannot be marked not applicable", out.stdout)
        out = p.cli("--decide", "preview", "defer", "--reason",
                    "a library, decide later", "--expected", "stale-rev")
        self.assertEqual(1, out.returncode)
        self.assertIn("changed since", out.stdout)
        self.assertFalse((p.work / "setup").exists())


class Decisions(unittest.TestCase):
    def payload(self, **kw):
        base = {"area": "preview", "decision": "not-applicable",
                "reason": "a library with nothing to preview",
                "expected": "none", "request": "req-00000001"}
        base.update(kw)
        return base

    def test_a_decision_is_recorded_attributed_and_revisitable(self):
        p = Project(self)
        done = p.call("decide", self.payload())["ok"]
        self.assertNotEqual("none", done["revision"])
        area = by_id(p.read())["preview"]
        self.assertEqual("not-applicable", area["status"])
        self.assertEqual("decision-needed", area["computed"])
        self.assertEqual("tester", area["decision"]["actor"])
        self.assertFalse(area["decision"]["stale"])
        self.assertIn("reopen", area["allowed"])
        again = p.call("decide", self.payload(decision="reopen",
                                              expected=done["revision"],
                                              request="req-00000002"))["ok"]
        self.assertEqual("decision-needed", by_id(p.read())["preview"]["status"])
        self.assertTrue(again["ok"])

    def test_a_stale_revision_is_a_conflict_and_changes_nothing(self):
        p = Project(self)
        p.call("decide", self.payload())
        before = (p.work / "setup" / "decisions.json").read_text()
        refused = p.call("decide", self.payload(area="delivery",
                                                request="req-00000003"))
        self.assertEqual(409, refused["status"])
        self.assertTrue(refused["stale"])
        self.assertEqual(before, (p.work / "setup" / "decisions.json").read_text())

    def test_a_replayed_request_answers_and_acts_once(self):
        p = Project(self)
        first = p.call("decide", self.payload())["ok"]
        second = p.call("decide", self.payload())["ok"]
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["revision"], second["revision"])
        refused = p.call("decide", self.payload(area="delivery"))
        self.assertEqual(409, refused["status"])

    def test_a_reason_is_required_and_scrubbed(self):
        p = Project(self)
        self.assertEqual(400, p.call("decide", self.payload(reason=""))["status"])
        token = "ghp_" + "a" * 30
        p.call("decide", self.payload(reason=f"later, token {token} here"))
        raw = (p.work / "setup" / "decisions.json").read_text()
        self.assertNotIn(token, raw)
        self.assertNotIn(str(p.dir), raw, "a path reached the shared file")

    def test_a_decision_goes_stale_when_its_facts_change(self):
        p = Project(self)
        p.call("decide", self.payload(area="instructions", decision="defer",
                                      reason="write them next sprint"))
        self.assertFalse(by_id(p.read())["instructions"]["decision"]["stale"])
        (p.app / "AGENTS.md").write_text("# rules\n")
        area = by_id(p.read())["instructions"]
        self.assertTrue(area["decision"]["stale"])
        self.assertEqual(["instructions"], p.read()["summary"]["stale"])

    def test_unknown_fields_survive_and_a_newer_version_is_read_only(self):
        p = Project(self)
        (p.work / "setup").mkdir()
        doc = {"kind": "bench-setup-decisions", "v": 1, "future": {"x": 1},
               "decisions": {"preview": {"status": "deferred", "reason": "r",
                                         "extra": "kept"}}, "tasks": {}}
        path = p.work / "setup" / "decisions.json"
        path.write_text(json.dumps(doc))
        rev = p.read()["revision"]
        p.call("decide", self.payload(decision="defer", expected=rev,
                                      reason="still later"))
        written = p.decisions()
        self.assertEqual({"x": 1}, written["future"])
        self.assertEqual("kept", written["decisions"]["preview"]["extra"])
        path.write_text(json.dumps(dict(doc, v=2)))
        found = p.read()
        self.assertTrue(found["readOnly"])
        refused = p.call("decide", self.payload(expected=found["revision"],
                                                request="req-00000009"))
        self.assertEqual(409, refused["status"])
        self.assertEqual(2, json.loads(path.read_text())["v"])

    def test_a_broken_file_is_not_written_over(self):
        p = Project(self)
        (p.work / "setup").mkdir()
        path = p.work / "setup" / "decisions.json"
        path.write_text("{not json")
        found = p.read()
        self.assertIn("does not parse", found["readOnly"])
        refused = p.call("decide", self.payload(expected=found["revision"]))
        self.assertEqual(409, refused["status"])
        self.assertEqual("{not json", path.read_text())


class SetupCards(unittest.TestCase):
    def test_a_setup_card_is_proposed_once_and_linked(self):
        p = Project(self)
        payload = {"area": "instructions", "expected": "none",
                   "request": "task-0000001"}
        done = p.call("create_task", payload)["ok"]
        self.assertEqual("08-write-project-instructions-for-coding-agents.md",
                         done["filename"])
        card = (p.work / "tasks" / "backlog" / done["filename"]).read_text()
        self.assertIn("**Status:** Backlog", card)
        self.assertFalse((p.app / "AGENTS.md").exists(),
                         "bench imposed instructions on the application")
        again = p.call("create_task", payload)["ok"]
        self.assertTrue(again["duplicate"])
        self.assertEqual(1, len(list((p.work / "tasks" / "backlog")
                                     .glob("08-*.md"))))
        area = by_id(p.read())["instructions"]
        self.assertEqual("08", area["task"]["number"])
        self.assertEqual("backlog", area["task"]["stage"])
        self.assertNotIn("create-task", area["allowed"])
        refused = p.call("create_task", dict(payload, request="task-0000002",
                                             expected=done["revision"]))
        self.assertEqual(409, refused["status"])

    def test_an_interrupted_creation_resumes_without_a_second_card(self):
        p = Project(self)
        # the card was written and the process died before the link was
        name = "08-write-project-instructions-for-coding-agents.md"
        (p.work / "tasks" / "backlog" / name).write_text("# 08 — x\n")
        (p.state / "setup").mkdir(parents=True)
        (p.state / "setup" / "operations.json").write_text(json.dumps({
            "task-0000001": {"kind": "task", "area": "instructions",
                             "status": "started", "filename": name,
                             "at": "2026-11-10T00:00:00Z"}}))
        found = p.read()
        self.assertEqual("task-0000001", found["interrupted"][0]["request"])
        done = p.call("create_task", {"area": "instructions",
                                      "expected": "whatever",
                                      "request": "task-0000001"})["ok"]
        self.assertEqual(name, done["filename"])
        self.assertEqual(1, len(list((p.work / "tasks" / "backlog")
                                     .glob("08-*.md"))))
        self.assertEqual([], p.read()["interrupted"])

    def test_areas_without_engineering_work_propose_no_card(self):
        p = Project(self)
        refused = p.call("create_task", {"area": "agent", "expected": "none",
                                         "request": "task-0000003"})
        self.assertEqual(400, refused["status"])

    def test_with_commit_moves_the_card_and_the_record_are_committed(self):
        p = Project(self)
        git(p.work, "add", "-A")
        git(p.work, "commit", "-q", "-m", "start")
        p.env["BOARD_COMMIT_MOVES"] = "1"
        done = p.call("create_task", {"area": "preview", "expected": "none",
                                      "request": "task-0000004"})["ok"]
        self.assertTrue(done["committed"])
        log = subprocess.run(["git", "-C", str(p.work), "log", "--format=%s"],
                             capture_output=True, text=True).stdout
        self.assertIn("board: setup preview task", log)
        self.assertIn("proposed by the setup guide", log)
        status = subprocess.run(["git", "-C", str(p.work), "status",
                                 "--porcelain"], capture_output=True,
                                text=True).stdout
        self.assertEqual("", status)


class TheHandoff(unittest.TestCase):
    def test_the_terminal_handoff_is_shown_as_history_by_area(self):
        p = Project(self)
        (p.state / "bootstrap").mkdir(parents=True)
        (p.state / "bootstrap" / "handoff.json").write_text(json.dumps({
            "kind": "bench-bootstrap-handoff", "v": 1, "status": "applied",
            "at": "2026-11-10T00:00:00Z", "guide": [
                {"id": "gh-missing", "title": "Pull requests need gh",
                 "how": "install gh"},
                {"id": "agent-cli-missing", "title": "Install the CLI",
                 "how": "token ghp_" + "b" * 30}],
            "operations": [{"id": "work-first-push", "outcome": "failed",
                            "summary": "push"}]}))
        found = p.read()["handoff"]
        self.assertTrue(found["present"])
        self.assertEqual({"gh-missing": "delivery", "agent-cli-missing": "agent"},
                         {g["id"]: g["area"] for g in found["guide"]})
        self.assertNotIn("ghp_" + "b" * 30, json.dumps(found))
        self.assertEqual("work-first-push", found["failed"][0]["id"])
        self.assertIn("read now", found["historical"])


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TheHttpRoutes(unittest.TestCase):
    def setUp(self):
        self.p = Project(self)
        self.port = free_port()
        env = dict(self.p.env, BOARD_PORT=str(self.port))
        self.log = open(self.p.dir / "board.log", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(self.p.core / "board.py"), "--port",
             str(self.port), "--no-open"], cwd=str(self.p.tool), env=env,
            stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.addCleanup(self._stop)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError((self.p.dir / "board.log").read_text())
            try:
                if self.get("/api/state")[0] == 200:
                    return
            except OSError:
                time.sleep(0.15)
        raise AssertionError("the board never answered")

    def _stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()

    def _open(self, req):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def get(self, path):
        return self._open(urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}"))

    def post(self, path, payload, headers=HEADERS):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(payload).encode(),
                                     method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        return self._open(req)

    def test_get_reads_post_writes_and_the_door_refuses_a_forged_write(self):
        status, body = self.get("/api/setup")
        self.assertEqual(200, status)
        self.assertEqual("bench-setup-state", body["setup"]["kind"])
        payload = {"area": "preview", "decision": "defer", "reason": "later on",
                   "expected": body["setup"]["revision"],
                   "request": "http-0000001"}
        status, _ = self.post("/api/setup/decide", payload,
                              headers={"Content-Type": "application/json"})
        self.assertEqual(403, status)
        self.assertFalse((self.p.work / "setup").exists())
        status, done = self.post("/api/setup/decide", payload)
        self.assertEqual(200, status, done)
        status, refused = self.post("/api/setup/decide",
                                    dict(payload, request="http-0000002"))
        self.assertEqual(409, status)
        self.assertTrue(refused["stale"])
        status, again = self.get("/api/setup")
        self.assertEqual("deferred",
                         by_id(again["setup"])["preview"]["status"])


if __name__ == "__main__":
    unittest.main()
