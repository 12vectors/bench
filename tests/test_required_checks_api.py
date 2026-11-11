"""Run required checks over a real board's HTTP API, through a restart
(task 101, design #94 §10.2–§10.3).

The disposable backend verify/browser serves — the candidate's
manager/core in a throwaway tool install, external layout, `gh` and the
vendor CLI both stubs — driven the way the sheet drives it: read the plan,
fire with the head it showed, follow the batch, stop it; then a board
killed while a check runs, started again, and the explicit recovery. No
live board, card or policy is read or written.

    python3 -m unittest tests.test_required_checks_api -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "verify"))

import benchverify  # noqa: E402

STEM = "97-run-required-checks"
FILE = f"{STEM}.md"
DOOR = {"Content-Type": "application/json", "X-Bench-Settings": "1"}

FLAGGED = """import os, sys, time
name = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
flag = open(os.path.join(here, name + ".flag")).read().strip()
open(os.path.join(here, name + ".count"), "a").write("ran\\n")
if flag == "hang":
    for _ in range(600):
        if os.path.exists(os.path.join(here, "release")):
            break
        time.sleep(0.1)
sys.exit(1 if flag == "fail" else 0)
"""


class RequiredChecksOverTheApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bench-required-api-")).resolve()
        env, _ = benchverify.child_env(cls.tmp / "env")
        cls.backend = backend = benchverify.Backend(
            REPO, cls.tmp / "fixture", env, sys.executable)
        cls.dir = cls.tmp / "checks"
        cls.dir.mkdir()
        script = cls.dir / "flagged.py"
        script.write_text(FLAGGED, encoding="utf-8")
        for name in ("unit", "build", "lint", "extra", "deps"):
            cls.flag(name, "pass")
        run = lambda name: f"{sys.executable} {script} {name}"  # noqa: E731
        (backend.tool / "manager" / "local" / "profiles.json").write_text(
            json.dumps({
                "v": 1,
                "prepare": [{"name": "deps", "run": run("deps")}],
                "checks": [
                    {"name": "unit", "run": run("unit"), "root": "worktree"},
                    {"name": "build", "run": run("build"), "root": "worktree",
                     "prepare": ["deps"]},
                    {"name": "lint", "run": run("lint"), "root": "worktree"},
                    {"name": "extra", "run": run("extra"), "root": "worktree"},
                ],
                "profiles": {"work": {"verify": ["unit", "build", "lint",
                                                 "extra"]}},
                "gates": {"acceptance": {"checks": ["unit", "build", "lint"]}},
            }), encoding="utf-8")
        cls.tree = backend.trees / STEM
        backend._git(backend.app, "worktree", "add", "-q", "-b", f"task/{STEM}",
                     str(cls.tree), "main")
        cls.commit("the implementation\n")
        (backend.work / "tasks" / "review" / FILE).write_text(
            "# Run required checks fixture\n\n**Status:** Review\n"
            "**Priority:** Low\n\nA disposable card for task 101.\n",
            encoding="utf-8")
        try:
            backend.start()
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        (cls.dir / "release").write_text("go", encoding="utf-8")
        cls.backend.stop()
        shutil.rmtree(cls.tmp, True)

    @classmethod
    def flag(cls, name: str, value: str) -> None:
        (cls.dir / f"{name}.flag").write_text(value, encoding="utf-8")

    @classmethod
    def runs(cls, name: str) -> int:
        try:
            return len((cls.dir / f"{name}.count").read_text().splitlines())
        except OSError:
            return 0

    @classmethod
    def commit(cls, text: str) -> None:
        (cls.tree / "code.txt").write_text(text, encoding="utf-8")
        cls.backend._git(cls.tree, "add", "-A")
        cls.backend._git(cls.tree, "commit", "-q", "-m", "fixture: work")

    # — the page's requests —

    def get(self, path: str) -> dict:
        status, body = self.backend.get(path)
        self.assertEqual(200, status, body[:500])
        return json.loads(body)

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            self.backend.url + path, data=json.dumps(payload).encode(),
            headers=DOOR, method="POST")
        try:
            with opener.open(request, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def plan(self) -> dict:
        return self.get(f"/api/verify/required?file={FILE}")["plan"]

    def fire(self) -> dict:
        shown = self.plan()
        status, body = self.post("/api/verify/required",
                                 {"file": FILE, "head": shown["head"]})
        self.assertEqual(200, status, body)
        return body["batch"]

    def until(self, predicate, timeout: float = 60) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            batch = self.plan()["batch"] or {}
            if predicate(batch):
                return batch
            time.sleep(0.2)
        self.fail(f"the batch never got there: {json.dumps(self.plan()['batch'])[:1500]}")

    def item(self, batch: dict, name: str) -> dict:
        return next(i for i in batch["items"] if i["check"] == name)

    def outcomes(self) -> dict:
        found = self.get(f"/api/evidence?file={FILE}&stage=review")
        return [(e["check"], e["outcome"]) for e in found.get("evidence") or []]

    def test_the_whole_journey(self):
        # 1. reading runs nothing
        plan = self.plan()
        self.assertIsNone(plan["refusal"], plan["why"])
        self.assertEqual(["request"] * 3, [i["state"] for i in plan["items"]])
        self.assertEqual([], self.outcomes())
        delivery = self.get("/api/state")["delivery"][FILE]
        self.assertEqual("run-required-checks", delivery["primary"]["action"])
        self.assertTrue(delivery["primary"]["enabled"])

        # 2. progress, then stop
        self.flag("unit", "hang")
        self.fire()
        running = self.until(lambda b: (b.get("items") or [{}])[0].get("live")
                             == "running")
        self.assertEqual("unit", running["summary"]["current"])
        status, _ = self.post("/api/verify/required/cancel", {"file": FILE})
        self.assertEqual(200, status)
        stopped = self.until(lambda b: b.get("state") == "stopped")
        self.assertEqual("cancelled", stopped["stop"])
        self.assertEqual("cancelled", self.item(stopped, "unit")["state"])
        self.assertEqual("not-requested", self.item(stopped, "build")["state"])
        self.assertEqual(0, self.runs("build"))

        # 3. explicit retry; a preparation failure is what it is
        self.flag("unit", "pass")
        self.flag("deps", "fail")
        self.fire()
        done = self.until(lambda b: b.get("state") == "finished")
        self.assertEqual("prepare-failed", self.item(done, "build")["state"])
        self.assertEqual("passed", self.item(done, "unit")["state"])
        self.assertEqual(0, self.runs("build"))
        after = self.plan()
        self.assertEqual("problem", next(i for i in after["items"]
                                         if i["check"] == "build")["state"])
        self.assertEqual("nothing-needed", after["refusal"])

        # 4. a restart mid-batch, and the recovery is a click
        self.flag("deps", "pass")
        self.flag("unit", "hang")
        self.commit("a newer commit\n")
        self.fire()
        self.until(lambda b: (b.get("items") or [{}])[0].get("live") == "running")
        before = {n: self.runs(n) for n in ("unit", "build", "lint")}
        self.backend.stop()
        self.backend.start(timeout=120)
        plan = self.plan()
        batch = plan["batch"]
        self.assertEqual("stopped", batch["state"])
        self.assertEqual("board-restart", batch["stop"])
        unit = self.item(batch, "unit")
        self.assertEqual("error", unit["state"])
        self.assertIn("board restarted", unit["why"])
        self.assertEqual(before, {n: self.runs(n) for n in before})
        reread = next(i for i in plan["items"] if i["check"] == "unit")
        self.assertEqual("restarted", reread["reason"])
        self.assertEqual("request", reread["state"])
        (self.dir / "release").write_text("go", encoding="utf-8")
        self.flag("unit", "pass")
        self.fire()
        done = self.until(lambda b: b.get("state") == "finished")
        self.assertEqual({"passed"}, {i["state"] for i in done["items"]})
        self.assertEqual(0, self.runs("extra"), "the optional check never ran")


if __name__ == "__main__":
    unittest.main()
