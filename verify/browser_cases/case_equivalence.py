"""Declared equivalence, in a browser (task 104, design #94 §5).

One disposable review card, its branch cut in the fixture's own
application repository, under a merge rule requiring `unit` — a `source`
check that `accepts` `unit-twin`, the same definition under another name.
The case asks the disposable backend to run `unit-twin` (through the same
`/api/verify` door a person's ↻ uses), and then reads what each surface
says: the backend's delivery reading meets the requirement *with the
twin's own name, evidence id and requester* (`reusedFrom`), the Overview's
merge requirement row says it was run as `unit-twin`, and Settings →
Checks lists what `unit` accepts. Nothing is applied from Settings; the
policy, the card and the branch are put back afterwards. Run through
verify/browser.
"""

import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "104-equivalence-fixture"
FILE = f"{STEM}.md"
OVERVIEW = "#drawer.open #dsec-overview"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def delivery() -> dict:
    status, body = S["backend"].get(f"/api/card/delivery?file={FILE}")
    if status != 200:
        raise bv.CheckFailed(f"/api/card/delivery answered {status}: {body[:300]!r}")
    return json.loads(body)["delivery"]


def load(width=1280, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    return b


class AnAcceptedRecordKeepsItsOwnName(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        true = shutil.which("true") or "/bin/true"
        cls.policy = backend.tool / "manager" / "local" / "profiles.json"
        cls.saved = cls.policy.read_bytes() if cls.policy.exists() else None
        cls.policy.write_text(json.dumps({
            "v": 1,
            "checks": [
                {"name": "unit", "run": true, "root": "source",
                 "accepts": ["unit-twin"]},
                {"name": "unit-twin", "run": true, "root": "source"}],
            "profiles": {"work": {"verify": ["unit", "unit-twin"]},
                         "review": {"verify": ["unit", "unit-twin"]}},
            "gates": {"acceptance": {"checks": ["unit"]}},
        }), encoding="utf-8")
        backend._git(backend.app, "checkout", "-q", "-b", f"task/{STEM}")
        backend._git(backend.app, "commit", "-q", "--allow-empty", "-m",
                     "fixture: the implementation commit")
        backend._git(backend.app, "checkout", "-q", "main")
        cls.card = backend.work / "tasks" / "review" / FILE
        cls.card.write_text(
            "# Equivalence fixture\n\n**Status:** Review\n**Priority:** Low\n\n"
            "A disposable card for the declared-equivalence case.\n",
            encoding="utf-8")
        status, body = backend.post("/api/verify", {
            "check": "unit-twin", "file": FILE, "stage": "review"}, timeout=120)
        if status != 200:
            raise bv.CheckFailed(f"/api/verify answered {status}: {body[:300]!r}")
        cls.evidence = json.loads(body)["evidence"]
        if cls.evidence.get("outcome") != "passed":
            raise bv.CheckFailed(f"the twin did not pass: {cls.evidence!r}"[:400])

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        cls.card.unlink(missing_ok=True)
        try:
            backend._git(backend.app, "branch", "-D", f"task/{STEM}")
        except bv.SetupError:
            pass
        if cls.saved is None:
            cls.policy.unlink(missing_ok=True)
        else:
            cls.policy.write_bytes(cls.saved)

    def test_1_the_backend_meets_unit_with_the_twins_provenance(self):
        found = delivery()
        rows = [r for r in found.get("requirements") or []
                if r.get("check") == "unit"]
        self.assertEqual(1, len(rows), found.get("requirements"))
        row = rows[0]
        self.assertEqual("met", row["class"], row)
        self.assertEqual(self.evidence["id"], row["evidence"])
        self.assertEqual("unit-twin", row["reusedFrom"]["check"])
        self.assertEqual(self.evidence["id"], row["reusedFrom"]["evidence"])
        S["metrics"]["equivalenceRow"] = {"class": row["class"],
                                          "reusedFrom": row["reusedFrom"]}

    def test_2_the_overview_row_says_it_ran_as_the_twin(self):
        b = load()
        sel = f'#board article.card[data-file="{FILE}"]'
        if not b.wait(f"!!document.querySelector('{sel}')", 15):
            raise bv.CheckFailed(f"{FILE} never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        deadline = time.time() + 30
        text = ""
        while time.time() < deadline:
            text = b.eval(f"(() => {{ const v = document.querySelector("
                          f"'{OVERVIEW} .dreqlist .dreqvia');"
                          " return v ? v.innerText : ''; })()") or ""
            if text:
                break
            time.sleep(0.5)
        shot("equivalence-overview-1280x900")
        self.assertIn("run as unit-twin", text)
        self.assertIn(self.evidence["id"], text)
        self.assertIn("accepts", text)
        b.key("Escape")

    def test_3_settings_lists_what_unit_accepts(self):
        b = load(1440, 900)
        b.click('#views button[data-view="settings"]')
        if not b.wait("document.querySelectorAll('#snav [data-ssection]').length >= 3", 15):
            raise bv.CheckFailed("the settings sections never arrived")
        before = self.policy.read_bytes()
        item = '#snav [data-ssection="verification"]'
        if not b.wait(f"!!document.querySelector('{item}')", 10):
            raise bv.CheckFailed("no Checks section in Settings")
        if b.eval(f"document.querySelector('{item}').offsetParent === null"):
            b.click('#snavtoggle')
            b.wait(f"document.querySelector('{item}').offsetParent !== null", 5)
        b.click(item)
        if not b.wait("document.querySelector('#sbody').textContent"
                      ".includes('Accepts evidence of')", 15):
            raise bv.CheckFailed("Settings → Checks does not list what unit accepts")
        shot("equivalence-settings-checks-1440x900")
        self.assertIn("unit-twin", b.eval("document.querySelector('#sbody').textContent"))
        self.assertEqual(before, self.policy.read_bytes(),
                         "reading Settings wrote nothing")


if __name__ == "__main__":
    unittest.main()
