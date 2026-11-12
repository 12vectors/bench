"""Opt-in sign-off and the advanced review choice, in a browser (task 103,
design #94 §8).

One disposable review card, its branch cut in the fixture's own
application repository, under a merge rule that asks for `signoff:
criteria`. The card face, the Overview and the backend read the same word
(`signoff-needed`); its action opens the Acceptance section and changes
nothing; a person tracking and confirming the criteria — through the
backend's own acceptance routes — makes it `ready-to-merge`. In Settings
the sign-off box is ticked only where the document says so, and the
Advanced policy editor's review boxes write the ordinary single word or
the advanced list, with a non-distinct pair refused at validation. Nothing
is applied from Settings, so the backend's document is exactly what this
case wrote, and every file is put back afterwards. Run through
verify/browser.
"""

import hashlib
import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "98-signoff-opt-in"
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


def load(width=1280, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    return b


def delivery() -> dict:
    status, body = S["backend"].get(f"/api/card/delivery?file={FILE}")
    if status != 200:
        raise bv.CheckFailed(f"/api/card/delivery answered {status}: {body[:300]!r}")
    return json.loads(body)["delivery"]


def acceptance_now() -> dict:
    status, body = S["backend"].get(f"/api/card/acceptance?file={FILE}&stage=review")
    if status != 200:
        raise bv.CheckFailed(f"/api/card/acceptance answered {status}: {body[:300]!r}")
    return json.loads(body)


def wait_for_word(word: str):
    b = S["browser"]
    js = (f"(S.state.delivery || {{}})[{json.dumps(FILE)}] && "
          f"S.state.delivery[{json.dumps(FILE)}].status.word === {json.dumps(word)}")
    deadline = time.time() + 40
    while time.time() < deadline:
        if b.wait(js, 4):
            return
        b.eval("loadState()")
    raise bv.CheckFailed(f"the page never read {word!r} for {FILE}")


def open_detail():
    b = S["browser"]
    sel = f'#board article.card[data-file="{FILE}"]'
    if not b.wait(f"!!document.querySelector('{sel}')", 15):
        raise bv.CheckFailed(f"{FILE} never reached the board")
    b.eval(f"document.querySelector('{sel}').focus()")
    b.key("Enter")
    if not b.wait(f"!!document.querySelector('{OVERVIEW} .dstatus')", 10):
        raise bv.CheckFailed("the Overview drew no status")
    return b


def close_detail():
    b = S["browser"]
    b.key("Escape")
    b.wait("!document.querySelector('#drawer').classList.contains('open')", 5)


def card_digest() -> str:
    backend = S["backend"]
    h = hashlib.sha256()
    for root in (backend.work / "tasks",
                 backend.tool / "manager" / "local" / "state" / "gates"):
        if root.exists():
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                h.update(str(path).encode())
                h.update(path.read_bytes())
    return h.hexdigest()


# the Settings helpers, as case_settings drives them

def open_settings(section: str, width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    b.click('#views button[data-view="settings"]')
    if not b.wait("document.querySelectorAll('#snav [data-ssection]').length >= 3", 15):
        raise bv.CheckFailed("the settings sections never arrived")
    item = f'#snav [data-ssection="{section}"]'
    if b.eval(f"document.querySelector('{item}').offsetParent === null"):
        b.click('#snavtoggle')
        b.wait(f"document.querySelector('{item}').offsetParent !== null", 5)
    b.click(item)
    if not b.wait(f"document.querySelector('{item}').getAttribute('aria-current') === 'page'", 5):
        raise bv.CheckFailed(f"the {section} section did not become current")
    return b


def field(path: str):
    return S["browser"].eval(
        f"(() => {{ const e = document.querySelector('[data-field=\"{path}\"]');"
        " return e ? {checked: !!e.checked, label: e.closest('label') ?"
        " e.closest('label').innerText : ''} : null; })()")


def set_field(path: str, value):
    S["browser"].eval(f"(() => {{ const e = document.querySelector('[data-field=\"{path}\"]');"
                      f" if (e.type === 'checkbox') e.checked = {json.dumps(bool(value))};"
                      f" else e.value = {json.dumps(str(value))};"
                      " e.dispatchEvent(new Event('change', {bubbles: true})); })()")


def discard():
    b = S["browser"]
    b.click('#s-discard')
    if not b.wait("S.draft === null && !!S.settings", 15):
        raise bv.CheckFailed("Discard did not drop the draft")


class SignoffIsOptIn(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.policy = backend.tool / "manager" / "local" / "profiles.json"
        cls.saved = cls.policy.read_bytes() if cls.policy.exists() else None
        cls.document = {"v": 1, "gates": {"acceptance": {"signoff": "criteria"}}}
        cls.policy.write_text(json.dumps(cls.document), encoding="utf-8")
        # an identity for the person's decisions, in the disposable work state
        backend._git(backend.work, "config", "user.name", "verify-operator")
        backend._git(backend.app, "checkout", "-q", "-b", f"task/{STEM}")
        backend._git(backend.app, "commit", "-q", "--allow-empty", "-m",
                     "fixture: the implementation commit")
        backend._git(backend.app, "checkout", "-q", "main")
        cls.card = backend.work / "tasks" / "review" / FILE
        cls.card.write_text(
            "# Sign-off fixture\n\n**Status:** Review\n**Priority:** Low\n\n"
            "A disposable card for the sign-off case.\n\n## Acceptance\n\n"
            "- [ ] The page reads well at 390 px\n"
            "- [ ] The copy was approved by a person\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        cls.card.unlink(missing_ok=True)
        for repo, args in ((backend.work, ("config", "--unset", "user.name")),
                           (backend.app, ("branch", "-D", f"task/{STEM}"))):
            try:
                backend._git(repo, *args)
            except bv.SetupError:
                pass
        if cls.saved is None:
            cls.policy.unlink(missing_ok=True)
        else:
            cls.policy.write_bytes(cls.saved)

    def test_1_the_face_the_overview_and_the_backend_say_signoff_needed(self):
        found = delivery()
        self.assertEqual(found["status"]["word"], "signoff-needed")
        self.assertEqual(found["primary"]["action"], "confirm-criteria")
        self.assertEqual(found["primary"]["label"], "Track acceptance")
        b = load()
        wait_for_word("signoff-needed")
        face = b.eval(f"(document.querySelector('#board article.card[data-file=\"{FILE}\"]')"
                      " || {}).innerText || ''")
        self.assertIn("Sign-off needed", face)
        open_detail()
        status = b.eval(f"(() => {{ const s = document.querySelector('{OVERVIEW} .dstatus');"
                        " const p = s.querySelector('.dsacts [data-dslot]');"
                        " return {word: s.dataset.word, heading: s.querySelector('h3').innerText,"
                        " primary: p ? p.innerText.trim() : null,"
                        " disabled: p ? !!p.disabled : null}; })()")
        shot("signoff-needed-overview-1280x900")
        self.assertEqual("signoff-needed", status["word"])
        self.assertIn("Sign-off needed", status["heading"])
        self.assertIn("Track acceptance", status["primary"])
        self.assertFalse(status["disabled"])
        S["metrics"]["signoffOverview"] = status
        close_detail()

    def test_2_its_action_opens_acceptance_and_writes_nothing(self):
        before = card_digest()
        b = load()
        wait_for_word("signoff-needed")
        open_detail()
        b.click(f"{OVERVIEW} .dstatus .dsacts [data-dslot]")
        if not b.wait("!!document.querySelector('#drawer.open #dsec-acceptance #accSignoff')"
                      " && document.querySelector('#drawer.open #dtab-acceptance')"
                      ".getAttribute('aria-selected') === 'true'", 15):
            raise bv.CheckFailed("Track acceptance did not open the Acceptance section "
                                 "with its sign-off line")
        line = b.eval("document.querySelector('#accSignoff').innerText")
        shot("signoff-acceptance-section-1280x900")
        self.assertIn("Sign-off required", line)
        self.assertIn("Track acceptance", line)
        self.assertEqual(before, card_digest(), "opening the section wrote something")
        close_detail()

    def test_3_tracking_and_confirming_meet_it(self):
        backend = S["backend"]
        now = acceptance_now()
        self.assertTrue(now["signoff"]["required"])
        status, body = backend.post("/api/card/acceptance/track", {
            "file": FILE, "stage": "review", "briefSha": now["briefSha"]})
        self.assertEqual(200, status, body)
        self.assertEqual(delivery()["primary"]["label"], "Confirm criteria")
        for c in acceptance_now()["criteria"]:
            status, body = backend.post("/api/card/acceptance/decide", {
                "file": FILE, "stage": "review", "criterion": c["id"],
                "state": "accepted", "rev": c["rev"], "basis": c["basis"],
                "note": "looked at it myself in the browser fixture"})
            self.assertEqual(200, status, body)
        found = delivery()
        self.assertEqual(found["status"]["word"], "ready-to-merge", found["status"])
        self.assertEqual(found["counts"], {"required": 1, "met": 1})
        b = load()
        wait_for_word("ready-to-merge")
        open_detail()
        shot("signoff-met-overview-1280x900")
        close_detail()

    def test_4_narrow_layout_keeps_the_status_and_the_action(self):
        b = load(390, 844)
        open_detail()
        m = b.eval("({viewport: {width: innerWidth, height: innerHeight},"
                   " scrollWidth: document.documentElement.scrollWidth, boxes: {}})")
        shot("signoff-overview-390x844")
        self.assertEqual([], bv.layout_problems(m))
        close_detail()


class SettingsOffersItWithoutCeremony(unittest.TestCase):
    """The document in force asks for sign-off on `acceptance` only. The
    editor shows it there and nowhere else; the Advanced editor writes the
    review choice as the ordinary word or the advanced list; nothing is
    applied, so the backend keeps exactly the document it had."""

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.policy = backend.tool / "manager" / "local" / "profiles.json"
        cls.saved = cls.policy.read_bytes() if cls.policy.exists() else None
        cls.policy.write_text(json.dumps(
            {"v": 1, "gates": {"acceptance": {"review": "bench",
                                              "signoff": "criteria"}}}),
            encoding="utf-8")
        cls.before = cls.policy.read_bytes()

    @classmethod
    def tearDownClass(cls):
        if cls.saved is None:
            cls.policy.unlink(missing_ok=True)
        else:
            cls.policy.write_bytes(cls.saved)

    def test_1_merge_rules_show_signoff_only_where_it_is_asked(self):
        b = open_settings("acceptance")
        text = b.eval("document.querySelector('#sbody').innerText")
        self.assertIn("sign-off: every criterion accepted by a person", text)
        self.assertIn("sign-off: not required", text)
        for scope, ticked in (("acceptance", True), ("member", False)):
            button = f'#sbody [data-sedit="gate"][data-sname="{scope}"]'
            if not b.wait(f"!!document.querySelector('{button}')", 10):
                raise bv.CheckFailed(f"no Edit button for the {scope} rule")
            b.click(button)
            if not b.wait("!!document.querySelector('#s-editor')", 5):
                raise bv.CheckFailed(f"Edit opened no editor for {scope}")
            box = field(f"gates:{scope}:signoff")
            self.assertIsNotNone(box, f"{scope}: no sign-off box")
            self.assertEqual(ticked, box["checked"], scope)
            self.assertIn("human decisions", box["label"])
            if scope == "acceptance":
                shot("signoff-settings-editor-1440x900")
            b.click('#s-close')
            b.wait("!document.querySelector('#s-editor')", 5)
        discard_if_dirty()

    def test_2_the_advanced_review_list_is_written_and_refused_as_it_should_be(self):
        b = open_settings("policy")
        if not b.wait("!!document.querySelector('[data-field=\"gates:acceptance:reviews:bench\"]')", 10):
            raise bv.CheckFailed("the Advanced policy editor drew no review boxes")
        self.assertTrue(field("gates:acceptance:reviews:bench")["checked"])
        self.assertFalse(field("gates:acceptance:reviews:separate-reviewer")["checked"])
        self.assertFalse(field("gates:member:reviews:bench")["checked"],
                         "no review is ticked where the document asks for none")
        set_field("gates:acceptance:reviews:separate-reviewer", True)
        self.assertEqual(["bench", "separate-reviewer"],
                         b.eval("S.draft.document.gates.acceptance.review"))
        b.click('#s-validate')
        if not b.wait("!!(S.draft && S.draft.checked)", 15):
            raise bv.CheckFailed("validate never came back")
        self.assertTrue(b.eval("S.draft.checked.ok"), b.eval("S.draft.checked.errors"))
        self.assertEqual(["bench", "separate-reviewer"],
                         b.eval("S.draft.checked.gate.scopes.acceptance.reviews"))
        shot("signoff-settings-review-list-1440x900")
        # one approval counted twice is refused at validation
        set_field("gates:acceptance:reviews:github", True)
        b.click('#s-validate')
        if not b.wait("!!(S.draft && S.draft.checked)", 15):
            raise bv.CheckFailed("validate never came back")
        self.assertFalse(b.eval("S.draft.checked.ok"))
        self.assertIn("gate-review-duplicate",
                      b.eval("S.draft.checked.errors.map(e => e.code)"))
        # back to one word: the ordinary scalar form, not a list of one
        set_field("gates:acceptance:reviews:github", False)
        set_field("gates:acceptance:reviews:separate-reviewer", False)
        self.assertEqual("bench", b.eval("S.draft.document.gates.acceptance.review"))
        discard()
        self.assertEqual(self.before, self.policy.read_bytes(),
                         "nothing in Settings was applied, so nothing was written")


def discard_if_dirty():
    b = S["browser"]
    if b.eval("!!(typeof S !== 'undefined' && S.draft)"):
        if b.eval("!!document.querySelector('#s-discard')"):
            discard()
        elif b.eval("!!document.querySelector('#sbody [data-sdraft=\"discard\"]')"):
            b.click('#sbody [data-sdraft="discard"]')
            b.wait("S.draft === null", 15)


if __name__ == "__main__":
    unittest.main()
