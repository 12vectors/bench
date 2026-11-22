"""Review is not admission (task 189), in a real browser.

A disposable Review card is written into the fixture's own work state,
with its branch cut in the fixture's own application repository and a
target branch of its own (`**Base branch:**`), so `main` is never moved.
The target then gains accepted work the card does not hold, and an
update attempt that conflicted is recorded the way
`integration.record_attempt` keeps it. Nothing reaches a remote, nothing
is launched, and everything is put back.

- The card's face says where it stands against its target, in words —
  *Conflicts with target — repair needed* · the target — on a line of its
  own under the review line, at 1280px and 390px, Daylight and Night:
  visible, not clipped to nothing, inside its card; screenshots kept.
- The card's Overview leads its merge requirements with **Against the
  target**: the target and its revision, that the head does not hold it,
  the accepted commit it would bring in, and that a provider's
  mergeability is not evidence the combination works.
- Reading posts nothing and writes nothing.
- A gated card (`UpdatingAgainstTheTarget`): *Ready to admit*, then
  *Target changed* with **Update against target and verify** as its
  primary action, the armed click updating its own branch on the
  fixture, and the combined head needing its own check and a fresh
  review. Then (review round 2): the target moving again while the review
  is still to come offers the update first, with the review beside it; an
  update refused for uncommitted work is said in an alarm toast and
  changes nothing; and the combined head's check reads *Integration check
  running*, then *Integration check failed*.

Not drawn here: the Orchestrator's admission queue (a card waiting on
another's admission into the same target) and the brief *Updating*
state, which lasts only while git merges; both are held by the backend
tests in `tests/test_admission.py` and `tests/test_orchestration_delivery.py`.
"""

import hashlib
import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "94-admission-conflict"
TARGET = "fixture-target-189"
PR = "https://github.com/example/fixture/pull/94"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


SEL = f'#board article.card[data-file="{STEM}.md"]'


def read() -> dict:
    status, body = S["backend"].get(f"/api/card/delivery?file={STEM}.md")
    if status != 200:
        raise bv.CheckFailed(f"/api/card/delivery answered {status}: {body[:300]!r}")
    return json.loads(body)["delivery"]


def face() -> dict | None:
    return S["browser"].eval(f"""(() => {{
      const el = document.querySelector('{SEL} .intstat');
      if (!el) return null;
      const card = el.closest('article').getBoundingClientRect();
      const r = el.getBoundingClientRect();
      const label = el.querySelector('.rvl');
      const cs = getComputedStyle(label);
      return {{ state: el.dataset.integration, text: label.innerText,
               all: el.innerText,
               visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden',
               labelClipped: label.scrollWidth > label.clientWidth + 1
                 && label.clientWidth < 40,
               inside: r.left >= card.left - 0.5 && r.right <= card.right + 0.5,
               color: cs.color, bg: getComputedStyle(el).backgroundColor }}; }})()""")


class AgainstTheTarget(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        git = backend._git
        app = backend.app
        cls.branches = []
        # the target: a branch of its own, so main is never moved
        git(app, "branch", TARGET, "main")
        cls.branches.append(TARGET)
        git(app, "checkout", "-q", "-b", f"task/{STEM}", TARGET)
        (app / "admission-shared.txt").write_text("the card's version\n", encoding="utf-8")
        git(app, "add", "admission-shared.txt")
        git(app, "commit", "-q", "-m", "fixture: the card's work")
        cls.branches.append(f"task/{STEM}")
        git(app, "checkout", "-q", TARGET)
        (app / "admission-shared.txt").write_text("accepted elsewhere\n", encoding="utf-8")
        git(app, "add", "admission-shared.txt")
        git(app, "commit", "-q", "-m", "accepted: another card's work")
        git(app, "checkout", "-q", "main")
        cls.card = backend.work / "tasks" / "review" / f"{STEM}.md"
        cls.card.write_text(
            f"# Admission fixture\n\n**Status:** Review\n**Priority:** Low\n"
            f"**Base branch:** {TARGET}\n**PR:** {PR}\n\n"
            f"A disposable card for the admission case.\n", encoding="utf-8")
        found = read()
        cls.head = found["candidate"]["commit"]
        cls.tip = (found.get("integration") or {}).get("targetTip")
        if not cls.head or not cls.tip:
            raise bv.SetupError(f"the fixture card read {found.get('integration')!r}")
        cls.record = backend.tool / "manager" / "local" / "state" / "integration" / f"{STEM}.json"
        cls.record.parent.mkdir(parents=True, exist_ok=True)
        cls.record.write_text(json.dumps({
            "v": 1, "kind": "bench-integration-attempts", "file": f"{STEM}.md",
            "attempts": [{"v": 1, "at": "2026-11-22T10:00:00Z", "head": cls.head,
                          "target": TARGET, "targetTip": cls.tip,
                          "outcome": "conflict", "actor": "person",
                          "conflicts": ["admission-shared.txt"]}]}),
            encoding="utf-8")
        # the card's text changes after its record did
        cls.card.write_text(cls.card.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        cls.card.unlink(missing_ok=True)
        cls.record.unlink(missing_ok=True)
        for ref in reversed(cls.branches):
            try:
                backend._git(backend.app, "branch", "-D", ref)
            except bv.SetupError:
                pass
        S["browser"].eval("localStorage.removeItem('bench-theme')")

    def load(self, width=1280, height=900, dark=None):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        if dark is not None:
            b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        b.eval("window.__posts = []; { const f = window.fetch; window.fetch = (u, o) => {"
               " if (o && o.method && o.method !== 'GET') window.__posts.push(String(u));"
               " return f.call(window, u, o); }; }")
        js = f"(document.querySelector('{SEL} .intstat') || {{}}).dataset?.integration === 'conflicts'"
        deadline = time.time() + 40
        while time.time() < deadline:
            if b.wait(js, 4):
                return b
            b.eval("loadState()")
        raise bv.CheckFailed(f"the face never read conflicts; it holds {face()}")

    def digest(self) -> str:
        backend = S["backend"]
        h = hashlib.sha256()
        roots = [backend.tool / "manager" / "local" / "state" / sub
                 for sub in ("gates", "evidence", "plans", "integration")]
        roots += [backend.work / "tasks", backend.app / ".git" / "refs"]
        for root in roots:
            for path in sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []:
                h.update(str(path).encode())
                h.update(path.read_bytes())
        return h.hexdigest()

    def test_1_the_face_says_it_in_words_at_both_widths_and_themes(self):
        found = read()
        self.assertEqual("conflicts", found["integration"]["state"])
        self.assertFalse(found["integration"]["fresh"])
        before = self.digest()
        seen = {}
        for width, height in ((1280, 900), (390, 844)):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    b = self.load(width, height, dark)
                    got = face()
                    self.assertIsNotNone(got)
                    self.assertEqual("Conflicts with target — repair needed", got["text"])
                    self.assertIn(TARGET, got["all"])
                    self.assertTrue(got["visible"], got)
                    self.assertFalse(got["labelClipped"], got)
                    self.assertTrue(got["inside"], got)
                    self.assertNotEqual(got["color"], got["bg"])
                    over = b.eval("document.documentElement.scrollWidth - innerWidth")
                    self.assertLessEqual(over, 1)
                    b.eval(f"document.querySelector('{SEL}').scrollIntoView({{block:'center'}})")
                    name = f"admission-face-{width}-{'night' if dark else 'day'}"
                    shot(name)
                    seen[name] = got["text"]
                    self.assertEqual([], b.eval("window.__posts"), "reading posted something")
        self.assertEqual(before, self.digest(), "reading wrote something")
        S["metrics"]["admission"] = seen

    def test_2_the_overview_says_against_the_target(self):
        before = self.digest()
        for width, height, dark in ((1280, 900, False), (390, 844, True)):
            with self.subTest(width=width, dark=dark):
                b = self.load(width, height, dark)
                b.eval(f"document.querySelector('{SEL}').click()")
                ok = b.wait("!!document.querySelector('.dint[data-integration=\"conflicts\"]')", 20)
                self.assertTrue(ok, "the Overview drew no Against the target block")
                text = b.eval("document.querySelector('.dint').innerText")
                self.assertIn("Against the target", text)
                self.assertIn("Conflicts with target", text)
                self.assertIn(self.tip[:12], text)
                self.assertIn("Review is not admission", text)
                self.assertIn("Provider mergeability", text)
                b.eval("document.querySelector('.dint').scrollIntoView({block:'start'})")
                shot(f"admission-overview-{width}-{'night' if dark else 'day'}")
                self.assertEqual([], b.eval("window.__posts"), "reading posted something")
                b.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        self.assertEqual(before, self.digest(), "reading wrote something")


STEM2 = "95-admission-update"
TARGET2 = "fixture-target-189b"
SEL2 = f'#board article.card[data-file="{STEM2}.md"]'


def read2() -> dict:
    status, body = S["backend"].get(f"/api/card/delivery?file={STEM2}.md")
    if status != 200:
        raise bv.CheckFailed(f"/api/card/delivery answered {status}: {body[:300]!r}")
    return json.loads(body)["delivery"]


class UpdatingAgainstTheTarget(unittest.TestCase):
    """A gated Review card through its states, in a real browser (review
    round 1 of task 189): *Ready to admit* while its head holds the target;
    *Target changed* once accepted work lands there, with **Update against
    target and verify** as the card's primary action; the armed click,
    which merges the target into the card's own branch on the disposable
    fixture (the only write here, and the one the click asks for); and
    afterwards the combined head needing its own check and a fresh review.

    The merge rule is the fixture's own `profiles.json`, restored after.
    The check is a stand-in that passes; the verdict is written as bench
    records one. The fixture's application has no remote, so nothing is
    pushed and no agent is launched by a clean update."""

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        git, app = backend._git, backend.app
        cls.policy = backend.tool / "manager" / "local" / "profiles.json"
        cls.saved = cls.policy.read_bytes() if cls.policy.exists() else None
        check = backend.base / "admission-check.py"
        check.write_text("print('ran 1 test — OK')\n", encoding="utf-8")
        cls.policy.write_text(json.dumps({
            "v": 1,
            "checks": [{"name": "unit", "run": f"{backend.python} {check}",
                        "root": "source"}],
            "profiles": {"work": {"verify": ["unit"]},
                         "review": {"verify": ["unit"]}},
            "gates": {"acceptance": {"checks": ["unit"], "review": "bench"}},
        }), encoding="utf-8")
        cls.branches = []
        git(app, "branch", TARGET2, "main")
        cls.branches.append(TARGET2)
        git(app, "checkout", "-q", "-b", f"task/{STEM2}", TARGET2)
        (app / "admission-card.txt").write_text("the card's work\n", encoding="utf-8")
        git(app, "add", "admission-card.txt")
        git(app, "commit", "-q", "-m", "fixture: the card's work")
        cls.branches.append(f"task/{STEM2}")
        git(app, "checkout", "-q", "main")
        cls.card = backend.work / "tasks" / "review" / f"{STEM2}.md"
        cls.card.write_text(
            f"# Admission update fixture\n\n**Status:** Review\n**Priority:** Low\n"
            f"**Base branch:** {TARGET2}\n**PR:** {PR}5\n\n"
            f"A disposable card for the admission update case.\n", encoding="utf-8")
        cls.gates_dir = backend.tool / "manager" / "local" / "state" / "gates"
        cls.gates_dir.mkdir(parents=True, exist_ok=True)
        cls.head = cls.checked_and_approved()

    @classmethod
    def checked_and_approved(cls) -> str:
        backend = S["backend"]
        status, body = backend.post("/api/verify", {
            "file": f"{STEM2}.md", "stage": "review", "check": "unit"}, timeout=120)
        if status != 200:
            raise bv.SetupError(f"/api/verify answered {status}: {body[:300]!r}")
        head = read2()["candidate"]["commit"]
        (cls.gates_dir / f"{STEM2}.verdicts.json").write_text(json.dumps([{
            "v": 1, "task": f"{STEM2}.md", "verdict": "APPROVE", "head": head,
            "agent": None, "actor": "agent", "reviewId": None, "note": "fixture",
            "recorded": time.time(), "published": None}]), encoding="utf-8")
        cls.card.write_text(cls.card.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return head

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        git, app = backend._git, backend.app
        cls.card.unlink(missing_ok=True)
        (cls.gates_dir / f"{STEM2}.verdicts.json").unlink(missing_ok=True)
        (backend.tool / "manager" / "local" / "state" / "integration"
         / f"{STEM2}.json").unlink(missing_ok=True)
        tree = backend.trees / STEM2
        if tree.exists():
            try:
                git(app, "worktree", "remove", "--force", str(tree))
            except bv.SetupError:
                pass
        for ref in reversed(cls.branches):
            try:
                git(app, "branch", "-D", ref)
            except bv.SetupError:
                pass
        if cls.saved is None:
            cls.policy.unlink(missing_ok=True)
        else:
            cls.policy.write_bytes(cls.saved)
        S["browser"].eval("localStorage.removeItem('bench-theme')")

    def load(self, state: str, width=1280, height=900, dark=None):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        if dark is not None:
            b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        b.eval("window.__posts = []; { const f = window.fetch; window.fetch = (u, o) => {"
               " if (o && o.method && o.method !== 'GET') window.__posts.push(String(u));"
               " return f.call(window, u, o); }; }")
        js = (f"!!document.querySelector('{SEL2}')" if state is None else
              f"(document.querySelector('{SEL2} .intstat') || {{}})"
              f".dataset?.integration === '{state}'")
        deadline = time.time() + 40
        while time.time() < deadline:
            if b.wait(js, 4):
                return b
            b.eval("loadState()")
        raise bv.CheckFailed(f"the face never read {state}; the backend says "
                             f"{read2().get('integration')}")

    def open(self, b):
        b.eval(f"document.querySelector('{SEL2}').focus()")
        b.key("Enter")
        if not b.wait("!!document.querySelector('#drawer.open #dsec-overview .dint')", 15):
            raise bv.CheckFailed("the Overview drew no Against the target block")

    def overview(self) -> dict:
        return S["browser"].eval("""(() => {
          const s = document.querySelector('#drawer.open #dsec-overview .dstatus');
          const i = document.querySelector('#drawer.open #dsec-overview .dint');
          const p = s && s.querySelector('.dsacts [data-dslot]');
          return { word: s && s.dataset.word, primary: p ? p.innerText.trim() : null,
                   disabled: p ? !!p.disabled : null,
                   state: i && i.dataset.integration, text: i ? i.innerText : '' }; })()""")

    def test_3_ready_to_admit_while_the_head_holds_the_target(self):
        found = read2()
        self.assertEqual("ready-to-merge", found["status"]["word"])
        self.assertEqual("ready-to-admit", found["integration"]["state"])
        for width, height, dark in ((1280, 900, False), (390, 844, True)):
            with self.subTest(width=width, dark=dark):
                b = self.load("ready-to-admit", width, height, dark)
                self.open(b)
                got = self.overview()
                self.assertEqual("ready-to-merge", got["word"])
                self.assertEqual("ready-to-admit", got["state"])
                self.assertIn("Ready to admit", got["text"])
                shot(f"admission-ready-{width}-{'night' if dark else 'day'}")
                self.assertEqual([], b.eval("window.__posts"), "reading posted something")
                b.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")

    def test_4_target_changed_offers_the_update_and_the_click_updates(self):
        backend = S["backend"]
        git, app = backend._git, backend.app
        # accepted work lands on the target the card does not hold
        git(app, "checkout", "-q", TARGET2)
        (app / "admission-accepted.txt").write_text("accepted\n", encoding="utf-8")
        git(app, "add", "admission-accepted.txt")
        git(app, "commit", "-q", "-m", "accepted: another card's work")
        git(app, "checkout", "-q", "main")
        self.card.write_text(self.card.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        found = read2()
        self.assertEqual("target-changed", found["status"]["word"])
        self.assertEqual("update-target", found["primary"]["action"])
        for width, height, dark in ((390, 844, False), (1280, 900, True)):
            with self.subTest(width=width, dark=dark):
                b = self.load("target-changed", width, height, dark)
                self.assertEqual("Target changed — integration check needed", b.eval(
                    f"document.querySelector('{SEL2} .intstat .rvl').innerText"))
                self.open(b)
                got = self.overview()
                self.assertEqual("target-changed", got["word"])
                self.assertIn("Update against target", got["primary"])
                self.assertFalse(got["disabled"])
                shot(f"admission-target-changed-{width}-{'night' if dark else 'day'}")
                self.assertEqual([], b.eval("window.__posts"), "reading posted something")
                b.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        # the armed click: the first arms, the second fires
        b = self.load("target-changed", 1280, 900, False)
        self.open(b)
        button = "#drawer.open #dsec-overview .dstatus .dsacts button[data-dslot]"
        b.click(button)
        self.assertEqual([], b.eval("window.__posts"), "arming posted something")
        b.click(button)
        if not b.wait("window.__posts.some(u => u.includes('/api/card/integration/update'))", 15):
            raise bv.CheckFailed(f"the second click posted nothing: {b.eval('window.__posts')}")
        deadline = time.time() + 60
        found = read2()
        while time.time() < deadline and found["candidate"]["commit"] == self.head:
            time.sleep(1)
            found = read2()
        new = found["candidate"]["commit"]
        self.assertNotEqual(self.head, new, "the card's branch was not updated")
        self.assertTrue(self.card.exists(), "the card stays in Review")
        self.assertTrue(found["integration"]["fresh"], found["integration"])
        # its old check and review are history: the combined head needs its own
        self.assertEqual("checks-needed", found["status"]["word"])
        self.assertEqual("integration-check-needed", found["integration"]["state"])
        # the face draws no integration line here (its pill already says
        # Checks needed); the Overview's Against the target block says why
        b = self.load(None, 1280, 900, False)
        self.open(b)
        if not b.wait("(document.querySelector('#drawer.open #dsec-overview .dint') || {})"
                      ".dataset?.integration === 'integration-check-needed'", 20):
            raise bv.CheckFailed(f"the Overview read {self.overview()}")
        self.assertIsNone(b.eval(f"document.querySelector('{SEL2} .intstat')"),
                          "the face repeats nothing the pill already says")
        got = self.overview()
        self.assertEqual("checks-needed", got["word"])
        shot("admission-after-update-1280-day")
        b.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        # the check passes on the combined head; a fresh review is still owed
        status, body = backend.post("/api/verify", {
            "file": f"{STEM2}.md", "stage": "review", "check": "unit"}, timeout=120)
        self.assertEqual(200, status, body[:300])
        self.card.write_text(self.card.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        found = read2()
        self.assertEqual("review-needed", found["status"]["word"],
                         "the approval of the old head is not this head's")
        self.assertTrue(found["integration"]["fresh"])

    def land(self, name: str) -> None:
        """Accepted work lands on the target the card does not hold."""
        backend = S["backend"]
        git, app = backend._git, backend.app
        git(app, "checkout", "-q", TARGET2)
        (app / name).write_text("accepted\n", encoding="utf-8")
        git(app, "add", name)
        git(app, "commit", "-q", "-m", f"accepted: {name}")
        git(app, "checkout", "-q", "main")
        self.card.write_text(self.card.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    def toast(self) -> dict:
        return S["browser"].eval("""(() => { const t = document.querySelector('#toast');
          return { shown: t.classList.contains('show'), err: t.classList.contains('err'),
                   text: document.querySelector('#toastmsg').innerText }; })()""")

    def test_5_the_update_comes_first_and_a_refused_one_is_said(self):
        """Review round 2: a card whose target moved while its review is
        still to come is offered the update first — the review stays
        offered beside it — and an update the server refuses is said in
        an alarm toast, with the branch left exactly as it was."""
        backend = S["backend"]
        self.land("admission-accepted-2.txt")
        found = read2()
        self.assertEqual("target-changed", found["status"]["word"], found["status"])
        self.assertEqual("update-target", found["primary"]["action"])
        self.assertIn("review-pr", [o.get("action") for o in found.get("others") or []])
        head = found["candidate"]["commit"]
        # uncommitted work in the card's worktree: the update must refuse
        stray = backend.trees / STEM2 / "stray-189.txt"
        stray.write_text("not committed\n", encoding="utf-8")
        try:
            for width, height, dark in ((1280, 900, False), (390, 844, True)):
                with self.subTest(width=width, dark=dark):
                    b = self.load("target-changed", width, height, dark)
                    self.open(b)
                    got = self.overview()
                    self.assertEqual("target-changed", got["word"])
                    self.assertIn("Update against target", got["primary"])
                    self.assertIn("still to come", b.eval(
                        "document.querySelector('#drawer.open #dsec-overview .dstatus').innerText"))
                    button = "#drawer.open #dsec-overview .dstatus .dsacts button[data-dslot]"
                    b.click(button)
                    b.click(button)
                    if not b.wait("document.querySelector('#toast').classList.contains('show')"
                                  " && document.querySelector('#toast').classList.contains('err')", 15):
                        raise bv.CheckFailed(f"no alarm toast after a refused update: {self.toast()}")
                    said = self.toast()
                    self.assertIn("uncommitted", said["text"])
                    shot(f"admission-update-refused-{width}-{'night' if dark else 'day'}")
                    self.assertEqual(head, read2()["candidate"]["commit"],
                                     "a refused update changed the branch")
                    b.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        finally:
            stray.unlink(missing_ok=True)

    def test_6_the_combined_check_running_then_failed(self):
        """The update's combined head: its required check running is
        *Integration check running*, and its failure there is *Integration
        check failed*, with the repair as the next step."""
        backend = S["backend"]
        head = read2()["candidate"]["commit"]
        status, body = backend.post("/api/card/integration/update",
                                    {"file": f"{STEM2}.md", "head": head}, timeout=60)
        self.assertEqual(200, status, body[:300])
        self.card.write_text(self.card.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual("integration-check-needed", read2()["integration"]["state"])
        # the check now waits on a sentinel, then fails: the combination broke
        sentinel = backend.base / "admission-hold-189"
        sentinel.write_text("hold\n", encoding="utf-8")
        check = backend.base / "admission-check.py"
        check.write_text(
            "import pathlib, sys, time\n"
            f"p = pathlib.Path({str(sentinel)!r})\n"
            "end = time.time() + 90\n"
            "while p.exists() and time.time() < end:\n"
            "    time.sleep(0.2)\n"
            "print('combined behaviour broken')\n"
            "sys.exit(1)\n", encoding="utf-8")
        import threading
        answer: dict = {}
        asking = threading.Thread(target=lambda: answer.update(r=backend.post(
            "/api/verify", {"file": f"{STEM2}.md", "stage": "review", "check": "unit"},
            timeout=150)), daemon=True)
        asking.start()
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                if read2()["integration"]["state"] == "integration-check-running":
                    break
                time.sleep(0.5)
            found = read2()
            self.assertEqual("integration-check-running", found["integration"]["state"],
                             found["integration"])
            self.assertEqual("checks-running", found["status"]["word"])
            b = self.load(None, 1280, 900, True)
            self.open(b)
            if not b.wait("(document.querySelector('#drawer.open #dsec-overview .dint') || {})"
                          ".dataset?.integration === 'integration-check-running'", 20):
                raise bv.CheckFailed(f"the Overview read {self.overview()}")
            self.assertIn("Integration check running", self.overview()["text"])
            shot("admission-check-running-1280-night")
            self.assertEqual([], b.eval("window.__posts"), "reading posted something")
            b.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        finally:
            sentinel.unlink(missing_ok=True)
            asking.join(timeout=120)
        self.card.write_text(self.card.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        found = read2()
        self.assertEqual("integration-check-failed", found["integration"]["state"],
                         found["integration"])
        self.assertEqual("check-failed", found["status"]["word"])
        for width, height, dark in ((390, 844, False), (1280, 900, False)):
            with self.subTest(width=width, dark=dark):
                b = self.load(None, width, height, dark)
                self.open(b)
                if not b.wait("(document.querySelector('#drawer.open #dsec-overview .dint') || {})"
                              ".dataset?.integration === 'integration-check-failed'", 20):
                    raise bv.CheckFailed(f"the Overview read {self.overview()}")
                got = self.overview()
                self.assertEqual("check-failed", got["word"])
                self.assertIn("Integration check failed", got["text"])
                shot(f"admission-check-failed-{width}-{'night' if dark else 'day'}")
                b.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")


if __name__ == "__main__":
    unittest.main()
