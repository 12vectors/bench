"""The merge-requirements (⚖ gate) sheet's override, in a browser (task 95).

Two disposable review cards under a policy that requires a check nobody
has run and allows overrides: one with no implementation commit (no
branch at all) and one whose branch holds a commit. The sheet draws the
backend's `overrides.eligible` — it does not work eligibility out itself —
so the first shows the Override action disabled with the reason, and the
second records an explicit override through the armed confirmation.

Everything is written in the disposable backend's own work state and
application repository, and put back afterwards. Run through verify/browser.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
DIALOG = "#sheetwrap .sheet"
NO_TARGET = "95-override-with-no-commit"
AT_COMMIT = "96-override-at-a-commit"
REASON = "the check runner is down for the fixture, proceeding on record"


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
    return b


def overrides_on_disk():
    gates = S["backend"].tool / "manager" / "local" / "state" / "gates"
    found = []
    for path in sorted(gates.glob("*.overrides.json")) if gates.is_dir() else []:
        found += json.loads(path.read_text(encoding="utf-8"))
    return found


def open_gate(stem: str):
    b = S["browser"]
    file = f"{stem}.md"
    if not b.wait(f"!!findTask({json.dumps(file)})", 15):
        b.eval("loadState()")
        if not b.wait(f"!!findTask({json.dumps(file)})", 15):
            raise bv.CheckFailed(f"{file} never reached the board")
    b.eval(f"gateSheet(findTask({json.dumps(file)}))")
    if not b.wait(f"!!document.querySelector('{DIALOG} #sh-nogate')", 20):
        raise bv.CheckFailed("the gate sheet never drew")
    return b


def close_sheet():
    b = S["browser"]
    b.click(f"{DIALOG} #sh-nogate")
    b.wait("!document.querySelector('#sheetwrap').classList.contains('open')", 5)


class OverrideNeedsATarget(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.policy = backend.tool / "manager" / "local" / "profiles.json"
        cls.saved = cls.policy.read_bytes() if cls.policy.exists() else None
        cls.policy.write_text(json.dumps({
            "v": 1,
            "checks": [{"name": "unit", "run": f"{backend.python} -c pass",
                        "root": "worktree"}],
            "profiles": {"work": {"verify": ["unit"]},
                         "review": {"verify": ["unit"]}},
            "gates": {"acceptance": {"checks": ["unit"]},
                      "overrides": {"allow": True}},
        }), encoding="utf-8")
        # An identity to record the override against, in the disposable
        # work state only.
        backend._git(backend.work, "config", "user.name", "verify-operator")
        # The card with a commit: a branch in the disposable application.
        backend._git(backend.app, "branch", f"task/{AT_COMMIT}")
        backend._git(backend.app, "commit", "-q", "--allow-empty", "-m",
                     "fixture: the implementation commit")
        backend._git(backend.app, "branch", "-f", f"task/{AT_COMMIT}", "HEAD")
        backend._git(backend.app, "reset", "-q", "--hard", "HEAD~1")
        cls.cards = []
        for stem, title in ((NO_TARGET, "Override with no commit"),
                            (AT_COMMIT, "Override at a commit")):
            card = backend.work / "tasks" / "review" / f"{stem}.md"
            card.write_text(f"# {title}\n\n**Status:** Review\n**Priority:** Low\n\n"
                            "A disposable card for the override sheet.\n",
                            encoding="utf-8")
            cls.cards.append(card)

    @classmethod
    def tearDownClass(cls):
        for card in cls.cards:
            card.unlink(missing_ok=True)
        if cls.saved is None:
            cls.policy.unlink(missing_ok=True)
        else:
            cls.policy.write_bytes(cls.saved)

    def test_1_no_commit_disables_override_with_the_reason(self):
        b = load()
        open_gate(NO_TARGET)
        state = b.eval(f"(() => {{ const btn = document.querySelector('{DIALOG} #sh-override');"
                       " return btn ? {disabled: btn.disabled, text: btn.innerText,"
                       " described: btn.getAttribute('aria-describedby')} : null; })()")
        shot("override-no-target-1440x900")
        self.assertIsNotNone(state, "the sheet should say why Override is unavailable")
        self.assertTrue(state["disabled"], state)
        self.assertIn("Not available", state["text"])
        self.assertIn("no implementation commit", state["text"])
        self.assertEqual("sh-override-why", state["described"])
        self.assertFalse(b.eval(f"!!document.querySelector('{DIALOG} #sh-why')"),
                         "no reason field is offered for an action that cannot run")
        # clicking the disabled action records nothing
        b.click(f"{DIALOG} #sh-override")
        b.click(f"{DIALOG} #sh-override")
        self.assertEqual([], [o for o in overrides_on_disk()
                              if o.get("task") == f"{NO_TARGET}.md"])
        close_sheet()

    def test_2_a_direct_call_cannot_bypass_it(self):
        status, body = S["backend"].post("/api/gates/override", {
            "file": f"{NO_TARGET}.md", "scope": "acceptance",
            "reason": REASON, "head": "f" * 40})
        self.assertNotEqual(200, status, body)
        self.assertIn(b"no implementation commit to override at", body)
        self.assertEqual([], [o for o in overrides_on_disk()
                              if o.get("task") == f"{NO_TARGET}.md"])

    def test_3_narrow_layout_keeps_the_reason_readable(self):
        b = load(390, 844)
        open_gate(NO_TARGET)
        m = b.eval("({viewport: {width: innerWidth, height: innerHeight},"
                   " scrollWidth: document.documentElement.scrollWidth, boxes: {}})")
        shot("override-no-target-390x844")
        self.assertEqual([], bv.layout_problems(m))
        self.assertIn("no implementation commit",
                      b.eval(f"document.querySelector('{DIALOG} #sh-override').innerText"))
        close_sheet()

    def test_4_a_valid_target_records_an_explicit_override(self):
        b = load()
        open_gate(AT_COMMIT)
        self.assertTrue(b.eval(f"(() => {{ const btn = document.querySelector("
                               f"'{DIALOG} #sh-override'); return !!btn && !btn.disabled; }})()"),
                        "an eligible gate offers the override")
        self.assertTrue(b.eval(f"!!document.querySelector('{DIALOG} #sh-why')"))
        shot("override-eligible-1440x900")
        b.eval(f"document.querySelector('{DIALOG} #sh-why').value = {json.dumps(REASON)}")
        b.click(f"{DIALOG} #sh-override")      # arm
        b.click(f"{DIALOG} #sh-override")      # fire
        if not b.wait("!!document.querySelector('#sheetwrap .sheet') && "
                      "document.querySelector('#sheetwrap .sheet').innerText"
                      ".includes('recorded, never a pass')", 20):
            raise bv.CheckFailed("the override was not recorded and redrawn")
        shot("override-recorded-1440x900")
        recorded = [o for o in overrides_on_disk() if o.get("task") == f"{AT_COMMIT}.md"]
        self.assertEqual(1, len(recorded), recorded)
        self.assertEqual("verify-operator", recorded[0]["actor"])
        self.assertTrue(recorded[0]["head"])
        self.assertEqual(REASON, recorded[0]["reason"])
        # in force now: the sheet offers no second one and says why
        text = b.eval(f"document.querySelector('{DIALOG}').innerText")
        self.assertIn("overridden", text.lower())
        self.assertFalse(b.eval(f"!!document.querySelector('{DIALOG} #sh-why')"))
        close_sheet()


if __name__ == "__main__":
    unittest.main()
