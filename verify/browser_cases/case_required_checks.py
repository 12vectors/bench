"""Run required checks, in a browser (task 101, design #94 §10.2–§10.3).

One disposable review card whose branch holds a commit, under a policy
that requires three checks (one of them prepared by a dependency step)
and offers a fourth that is optional. Each check is a small script whose
verdict is a flag file this case writes, so the case decides what each
run finds without touching anything outside the disposable backend.

In order, against the same card:

1. the confirmation lists what would be requested, with its side
   effects, and opening it runs nothing and makes no checkout;
2. firing it shows the run in progress, and Stop cancels the check in
   flight and leaves the rest *not requested* — nothing retried;
3. the explicit retry runs them again; a failing preparation step reads
   *preparation failed*, never passed and never "not run";
4. a board restart mid-batch reads *no result — the board restarted*,
   and one more click is the recovery; the optional check never ran.

Everything is written in the disposable backend's own work state,
application repository and tool install, and put back afterwards. Run
through verify/browser.
"""

import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
SHEET = "#sheetwrap #reqsheet"
STEM = "97-run-required-checks"
FILE = f"{STEM}.md"

FLAGGED = """import os, sys, time
name = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
flag = open(os.path.join(here, name + ".flag")).read().strip()
open(os.path.join(here, name + ".count"), "a").write("ran\\n")
if flag == "hang":
    print("working", flush=True)
    # bounded, so a check orphaned by the restart scenario goes by itself
    for _ in range(600):
        if os.path.exists(os.path.join(here, "release")):
            break
        time.sleep(0.1)
print(name, flag)
sys.exit(1 if flag == "fail" else 0)
"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def load(width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    if not b.wait(f"!!findTask({json.dumps(FILE)})", 15):
        raise bv.CheckFailed(f"{FILE} never reached the board")
    return b


def open_sheet():
    b = S["browser"]
    b.eval(f"requiredSheet(findTask({json.dumps(FILE)}))")
    if not b.wait(f"!!document.querySelector('{SHEET} #sh-noreq')", 20):
        raise bv.CheckFailed("the Run required checks sheet never drew")
    return b


def sheet_text() -> str:
    return S["browser"].eval(
        f"(document.querySelector('{SHEET}') || {{innerText: ''}}).innerText")


def wait_text(fragment: str, timeout: float = 30) -> None:
    if not S["browser"].wait(
            f"(document.querySelector('{SHEET}') || {{innerText: ''}})"
            f".innerText.includes({json.dumps(fragment)})", timeout):
        raise bv.CheckFailed(f"the sheet never said {fragment!r}; it says:\n"
                             + sheet_text()[:1500])


def fire(button: str):
    b = S["browser"]
    b.click(f"{SHEET} #{button}")      # arm
    b.click(f"{SHEET} #{button}")      # fire


def close():
    b = S["browser"]
    b.key("Escape")
    if not b.wait("!document.querySelector('#sheetwrap').classList.contains('open')", 5):
        b.eval("closeSheet()")


def plan() -> dict:
    status, body = S["backend"].get(f"/api/verify/required?file={FILE}")
    if status != 200:
        raise bv.CheckFailed(f"/api/verify/required answered {status}: {body[:300]!r}")
    return json.loads(body)["plan"]


def evidence() -> list:
    status, body = S["backend"].get(f"/api/evidence?file={FILE}&stage=review")
    if status != 200:
        raise bv.CheckFailed(f"/api/evidence answered {status}: {body[:300]!r}")
    return json.loads(body).get("evidence") or []


class RunRequiredChecks(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.dir = backend.base / "required-checks"
        cls.dir.mkdir(exist_ok=True)
        script = cls.dir / "flagged.py"
        script.write_text(FLAGGED, encoding="utf-8")
        for name in ("unit", "build", "lint", "extra", "deps"):
            cls.flag(name, "pass")
        run = lambda name: f"{backend.python} {script} {name}"  # noqa: E731
        cls.policy = backend.tool / "manager" / "local" / "profiles.json"
        cls.saved = cls.policy.read_bytes() if cls.policy.exists() else None
        cls.policy.write_text(json.dumps({
            "v": 1,
            "prepare": [{"name": "deps", "run": run("deps")}],
            "checks": [
                {"name": "unit", "run": run("unit"), "root": "worktree"},
                {"name": "build", "run": run("build"), "root": "worktree",
                 "prepare": ["deps"]},
                {"name": "lint", "run": run("lint"), "root": "worktree"},
                {"name": "extra", "run": run("extra"), "root": "worktree"},
            ],
            "profiles": {"work": {"verify": ["unit", "build", "lint", "extra"]},
                         "review": {"verify": ["unit"]}},
            "gates": {"acceptance": {"checks": ["unit", "build", "lint"]}},
        }), encoding="utf-8")
        cls.tree = backend.trees / STEM
        backend._git(backend.app, "worktree", "add", "-q", "-b", f"task/{STEM}",
                     str(cls.tree), "main")
        cls.commit("the implementation\n")
        cls.card = backend.work / "tasks" / "review" / FILE
        cls.card.write_text("# Run required checks fixture\n\n**Status:** Review\n"
                            "**Priority:** Low\n\nA disposable card for task 101.\n",
                            encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        (cls.dir / "release").write_text("go", encoding="utf-8")
        status, _ = backend.get(f"/api/verify/required?file={FILE}")
        if status == 200:
            backend.post("/api/verify/required/cancel", {"file": FILE})
        time.sleep(0.5)
        cls.card.unlink(missing_ok=True)
        for args in (("worktree", "remove", "--force", str(cls.tree)),
                     ("branch", "-D", f"task/{STEM}")):
            try:
                backend._git(backend.app, *args)
            except bv.SetupError:
                pass
        if cls.saved is None:
            cls.policy.unlink(missing_ok=True)
        else:
            cls.policy.write_bytes(cls.saved)

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
        backend = S["backend"]
        (cls.tree / "code.txt").write_text(text, encoding="utf-8")
        backend._git(cls.tree, "add", "-A")
        backend._git(cls.tree, "-c", "user.name=verify", "-c",
                     "user.email=verify@example.invalid", "commit", "-q", "-m",
                     "fixture: work")

    def test_1_the_confirmation_lists_side_effects_and_runs_nothing(self):
        b = load()
        primary = b.eval(f"(deliveryOf(findTask({json.dumps(FILE)})) || {{}}).primary")
        self.assertEqual("run-required-checks", (primary or {}).get("action"), primary)
        self.assertTrue(primary["enabled"], primary)
        trees = sorted(p.name for p in S["backend"].trees.iterdir())
        open_sheet()
        text = sheet_text()
        shot("required-checks-confirm-1440x900")
        self.assertIn("Will be requested, in this order", text)
        for name in ("unit", "build", "lint"):
            self.assertIn(name, text)
        self.assertNotIn("extra", text, "an optional check is not offered")
        self.assertIn("Side effects, in full", text)
        self.assertIn("moves no card", text)
        self.assertIn("Preparation steps", text)
        self.assertIn("preparation first: deps", text)
        self.assertTrue(b.eval(f"!!document.querySelector('{SHEET} #sh-reqgo')"))
        # reading it ran nothing and made no checkout
        self.assertEqual([], evidence())
        self.assertEqual(trees, sorted(p.name for p in S["backend"].trees.iterdir()))
        self.assertEqual(0, sum(self.runs(n) for n in ("unit", "build", "lint", "extra")))
        close()
        load(390, 844)
        open_sheet()
        m = b.eval("({viewport: {width: innerWidth, height: innerHeight},"
                   " scrollWidth: document.documentElement.scrollWidth, boxes: {}})")
        shot("required-checks-confirm-390x844")
        self.assertEqual([], bv.layout_problems(m))
        close()

    def test_2_progress_then_stop_leaves_the_rest_not_requested(self):
        self.flag("unit", "hang")
        b = load()
        open_sheet()
        fire("sh-reqgo")
        wait_text("Running now")
        wait_text("running the check")
        self.assertTrue(b.eval(f"!!document.querySelector('{SHEET} #sh-reqstop')"))
        shot("required-checks-running-1440x900")
        S["metrics"]["requiredRunning"] = plan()["batch"]["summary"]
        fire("sh-reqstop")
        wait_text("Stopped:")
        text = sheet_text()
        shot("required-checks-stopped-1440x900")
        self.assertIn("not requested", text)
        found = {e["check"]: e["outcome"] for e in evidence()}
        self.assertEqual("cancelled", found.get("unit"), found)
        self.assertNotIn("build", found, "nothing after the stop was requested")
        self.assertNotIn("lint", found)
        self.assertEqual(0, self.runs("build"))
        close()

    def test_3_retry_and_a_preparation_failure_read_as_what_they_are(self):
        self.flag("unit", "pass")
        self.flag("deps", "fail")
        b = load()
        open_sheet()
        self.assertIn("Run required checks again", sheet_text(),
                      "after a stop, the retry is a person's click")
        fire("sh-reqgo")
        wait_text("Finished", 60)
        text = sheet_text()
        shot("required-checks-prepare-failed-1440x900")
        found = {e["check"]: e["outcome"] for e in evidence()
                 if e["outcome"] not in ("cancelled",)}
        self.assertEqual("prepare-failed", found.get("build"), found)
        self.assertEqual("passed", found.get("unit"), found)
        self.assertEqual("passed", found.get("lint"), found)
        self.assertEqual(0, self.runs("build"), "the check itself never ran")
        self.assertIn("preparation", text.lower())
        close()
        # read again: the failure is shown as one, not requested again
        open_sheet()
        text = sheet_text()
        self.assertIn("not requested — its last result did not pass", text)
        self.assertFalse(b.eval(f"!!document.querySelector('{SHEET} #sh-reqgo')"),
                         "nothing is left that a click would request")
        word = b.eval(f"deliveryOf(findTask({json.dumps(FILE)})).status.word")
        self.assertNotIn(word, ("checks-needed", "ready-to-merge"), word)
        close()

    def test_4_a_restart_reads_no_result_and_a_click_recovers(self):
        backend = S["backend"]
        self.flag("deps", "pass")
        self.flag("unit", "hang")
        self.commit("a newer commit\n")       # every requirement is needed again
        b = load()
        open_sheet()
        fire("sh-reqgo")
        wait_text("running the check")
        before = {n: self.runs(n) for n in ("unit", "build", "lint")}
        backend.stop()
        # generous: a loaded machine restarts a board slowly, and that is
        # not what this case measures
        backend.start(timeout=120)
        b = load()
        open_sheet()
        wait_text("this board stopped during the batch")
        text = sheet_text()
        shot("required-checks-restarted-1440x900")
        self.assertIn("no result — the board restarted", text)
        self.assertIn("Run required checks again", text)
        self.assertEqual(before, {n: self.runs(n) for n in before},
                         "nothing was asked again by itself")
        p = plan()
        unit = next(i for i in p["items"] if i["check"] == "unit")
        self.assertEqual("restarted", unit["reason"])
        self.assertEqual("request", unit["state"])
        # the explicit recovery
        (self.dir / "release").write_text("go", encoding="utf-8")
        self.flag("unit", "pass")
        fire("sh-reqgo")
        wait_text("Finished", 60)
        shot("required-checks-recovered-1440x900")
        latest = plan()["batch"]
        self.assertEqual({"passed"}, {i["state"] for i in latest["items"]
                                       if i.get("state") not in ("met",)}, latest)
        self.assertEqual(0, self.runs("extra"), "the optional check never ran")
        close()
        if not b.wait(f"deliveryOf(findTask({json.dumps(FILE)})).status.word"
                      " === 'ready-to-merge'", 20):
            b.eval("loadState()")
        S["metrics"]["requiredRecovered"] = b.eval(
            f"deliveryOf(findTask({json.dumps(FILE)})).status")


if __name__ == "__main__":
    unittest.main()
