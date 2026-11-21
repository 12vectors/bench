"""↻ act on PR on a phase card in Review (task 174), in a real browser.

Three disposable phase cards are written into the fixture's work state,
each with its `phase/<stem>` branch in the fixture's application repository
and one member card already delivered. Bench's own REQUEST CHANGES verdict
is written at each phase's integrated head, as `gates.record_verdict` keeps
it.

- **Offered** — the open-PR phase's Overview leads with *Changes
  requested* and offers **↻ act on PR**, enabled, with the integration
  branch it repairs drawn beside it on the page; nothing says "Move back
  to in-progress". At 1280px and 390px, Daylight and Night.
- **Disabled, with its reason** — the phase whose integration PR this board
  observed closed offers the same action disabled, its reason drawn beside
  it in words, at both widths and in both themes.
- **Confirmation** — the first click arms the action and fires nothing;
  reading and arming post nothing and write nothing.
- **Running and handoff** — the second click fires `/api/agent/act-pr`
  for the phase card, and the page draws the action working. The fixture's
  vendor CLI is a stub that refuses, so the run starts on the phase branch
  and ends failed: the card stays in Review, its delivery status hands off
  to *Run failed* with **Read what happened**, and no task branch or new
  card is created. Once at 1280px in Daylight, once at 390px in Night,
  each on a phase of its own.

Nothing reaches a remote and everything is put back. Run through
verify/browser.
"""

import hashlib
import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
PR = "https://github.com/example/fixture/pull/{n}"
OPEN = "96-phase-repair-open"
CLOSED = "97-phase-repair-closed"
NARROW = "98-phase-repair-narrow"
MEMBERS = {OPEN: "94-phase-repair-member-a", CLOSED: "95-phase-repair-member-b",
           NARROW: "93-phase-repair-member-c"}


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def read(stem: str) -> dict:
    status, body = S["backend"].get(f"/api/card/delivery?file={stem}.md")
    if status != 200:
        raise bv.CheckFailed(f"/api/card/delivery answered {status}: {body[:300]!r}")
    return json.loads(body)["delivery"]


def card_sel(stem: str) -> str:
    return f'#board article.card[data-file="{stem}.md"]'


def overview() -> dict | None:
    return S["browser"].eval("""(() => {
      const s = document.querySelector('#drawer.open #dsec-overview .dstatus');
      if (!s) return null;
      const p = s.querySelector('.dsacts [data-dslot]');
      const why = p && p.getAttribute('aria-describedby')
        ? document.getElementById(p.getAttribute('aria-describedby')) : null;
      const r = p ? p.getBoundingClientRect() : null;
      const t = p && p.parentElement ? p.parentElement.querySelector('.dtarget') : null;
      return { word: s.dataset.word, heading: s.querySelector('h3').innerText,
               target: t ? t.innerText : null,
               primary: p ? p.innerText.trim() : null,
               disabled: p ? !!p.disabled : null,
               armed: p ? p.classList.contains('armed') : null,
               why: why ? why.innerText : null,
               visible: !!r && r.width > 0 && r.height > 0,
               overflow: document.documentElement.scrollWidth > innerWidth + 1,
               text: s.innerText }; })()""")


class PhaseRepairInTheBrowser(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.gates = backend.tool / "manager" / "local" / "state" / "gates"
        cls.gates.mkdir(parents=True, exist_ok=True)
        cls.files, cls.branches, cls.heads = [], [], {}
        for n, (phase, member) in enumerate(MEMBERS.items()):
            done = backend.work / "tasks" / "done" / f"{member}.md"
            done.write_text(f"# {member[:2]} — Phase repair member\n\n**Status:** Done\n"
                            f"**Priority:** Low\n\nA delivered member.\n",
                            encoding="utf-8")
            card = backend.work / "tasks" / "review" / f"{phase}.md"
            card.write_text(
                f"# {phase[:2]} — Phase repair fixture {n + 1}\n\n**Status:** Review\n"
                f"**Priority:** Low\n**Type:** Phase\n**PR:** {PR.format(n=phase[:2])}\n\n"
                f"A disposable phase card for the phase repair case.\n\n"
                f"## Cards\n\n- {member[:2]} — Phase repair member\n",
                encoding="utf-8")
            cls.files += [done, card]
            ref = f"phase/{phase}"
            backend._git(backend.app, "checkout", "-q", "-b", ref)
            backend._git(backend.app, "commit", "-q", "--allow-empty", "-m",
                         f"fixture: {member} integrated")
            backend._git(backend.app, "checkout", "-q", "main")
            cls.branches.append(ref)
        for phase in MEMBERS:
            cls.heads[phase] = read(phase)["candidate"]["commit"]
            cls.verdict(phase, cls.heads[phase])
        # this board observed the second phase's integration PR closed
        (cls.gates / f"{CLOSED}.pr.json").write_text(json.dumps({
            "head": cls.heads[CLOSED], "prState": "CLOSED", "merged": False,
            "url": PR.format(n=CLOSED[:2]), "observed": time.time(),
            "provider": "github"}), encoding="utf-8")
        cls.touch(CLOSED)

    @classmethod
    def verdict(cls, stem: str, head: str):
        path = cls.gates / f"{stem}.verdicts.json"
        path.write_text(json.dumps([{
            "v": 1, "task": f"{stem}.md", "verdict": "REQUEST CHANGES",
            "head": head, "agent": None, "actor": "agent", "reviewId": None,
            "note": "fixture", "recorded": time.time(), "published": None}]),
            encoding="utf-8")
        cls.touch(stem)

    @classmethod
    def touch(cls, stem: str):
        card = S["backend"].work / "tasks" / "review" / f"{stem}.md"
        card.write_text(card.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        for path in cls.files:
            path.unlink(missing_ok=True)
        for stem in MEMBERS:
            for suffix in ("verdicts.json", "pr.json"):
                (cls.gates / f"{stem}.{suffix}").unlink(missing_ok=True)
            tree = backend.trees / stem
            if tree.exists():
                try:
                    backend._git(backend.app, "worktree", "remove", "--force", str(tree))
                except bv.SetupError:
                    pass
        for ref in cls.branches:
            try:
                backend._git(backend.app, "branch", "-D", ref)
            except bv.SetupError:
                pass
        S["browser"].eval("localStorage.removeItem('bench-theme')")

    # — helpers —

    def load(self, width=1280, height=900, dark=None):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        if dark is not None:
            b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        b.eval("window.__posts = []; { const f = window.fetch; window.fetch = (u, o) => {"
               " if (o && o.method && o.method !== 'GET') window.__posts.push(String(u));"
               " return f.call(window, u, o); }; }")
        if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
            raise bv.CheckFailed("the board never drew its stage columns")
        return b

    def wait_word(self, stem: str, word: str):
        b = S["browser"]
        js = (f"(S.state.delivery || {{}})[{json.dumps(stem + '.md')}] && "
              f"S.state.delivery[{json.dumps(stem + '.md')}].status.word === {json.dumps(word)}")
        deadline = time.time() + 40
        while time.time() < deadline:
            if b.wait(js, 4):
                return
            b.eval("loadState()")
        raise bv.CheckFailed(f"{stem}: the page never read {word!r}")

    def open_detail(self, stem: str):
        b = S["browser"]
        sel = card_sel(stem)
        if not b.wait(f"!!document.querySelector('{sel}')", 15):
            raise bv.CheckFailed(f"{stem} never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        if not b.wait("!!document.querySelector('#drawer.open #dsec-overview .dstatus')", 10):
            raise bv.CheckFailed(f"{stem}: the Overview drew no status")
        b.wait("document.querySelector('#drawer').getAnimations()"
               ".every(a => a.playState === 'finished')", 5)
        return b

    def digest(self) -> str:
        backend = S["backend"]
        h = hashlib.sha256()
        roots = [backend.tool / "manager" / "local" / "state" / sub
                 for sub in ("gates", "evidence", "plans", "reviews")]
        roots += [backend.work / "tasks", backend.app / ".git" / "refs"]
        for root in roots:
            for path in sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []:
                h.update(str(path).encode())
                h.update(path.read_bytes())
        return h.hexdigest()

    # — the cases —

    def test_1_the_open_pr_phase_offers_act_on_pr_at_both_widths_and_themes(self):
        found = read(OPEN)
        self.assertEqual("changes-requested", found["status"]["word"])
        self.assertEqual(("act-pr", True, f"phase/{OPEN}"),
                         (found["primary"]["action"], found["primary"]["enabled"],
                          found["primary"].get("target")))
        before = self.digest()
        for width, height in ((1280, 900), (390, 844)):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    self.load(width, height, dark)
                    self.wait_word(OPEN, "changes-requested")
                    self.open_detail(OPEN)
                    ov = overview()
                    self.assertEqual("changes-requested", ov["word"])
                    self.assertIn("act on PR", ov["primary"])
                    self.assertFalse(ov["disabled"])
                    self.assertIn(f"phase/{OPEN}", ov["target"] or "",
                                  "the page names the branch it repairs")
                    self.assertTrue(ov["visible"])
                    self.assertFalse(ov["overflow"], "the page scrolls sideways")
                    self.assertNotIn("Move back to in-progress", ov["text"])
                    shot(f"phase-repair-offered-{width}-{'night' if dark else 'day'}")
                    S["browser"].key("Escape")
        self.assertEqual([], S["browser"].eval("window.__posts"),
                         "reading posted something")
        self.assertEqual(before, self.digest(), "reading wrote something")

    def test_2_a_closed_integration_pr_is_disabled_with_its_reason(self):
        found = read(CLOSED)
        self.assertEqual(("act-pr", False), (found["primary"]["action"],
                                             found["primary"]["enabled"]))
        for width, height in ((1280, 900), (390, 844)):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    self.load(width, height, dark)
                    self.wait_word(CLOSED, found["status"]["word"])
                    self.open_detail(CLOSED)
                    ov = overview()
                    self.assertIn("act on PR", ov["primary"])
                    self.assertTrue(ov["disabled"])
                    self.assertIn("closed", ov["why"] or "")
                    self.assertFalse(ov["overflow"], "the page scrolls sideways")
                    shot(f"phase-repair-disabled-{width}-{'night' if dark else 'day'}")
                    S["browser"].key("Escape")

    def test_3_confirm_then_run_and_hand_off(self):
        # one phase per layout, since a fired repair is spent: desktop in
        # Daylight, a phone in Night
        for stem, width, height, dark in ((OPEN, 1280, 900, False),
                                          (NARROW, 390, 844, True)):
            with self.subTest(width=width, dark=dark):
                self.confirm_run_and_hand_off(stem, width, height, dark)

    def confirm_run_and_hand_off(self, stem, width, height, dark):
        theme = "night" if dark else "day"
        b = self.load(width, height, dark)
        self.wait_word(stem, "changes-requested")
        self.open_detail(stem)
        slot = "#dsec-overview .dsacts [data-dslot]"
        b.click(slot)
        self.assertTrue(b.wait(f"document.querySelector('{slot}').classList.contains('armed')", 5),
                        "the first click did not arm the action")
        shot(f"phase-repair-armed-{width}-{theme}")
        self.assertEqual([], b.eval("window.__posts"), "arming fired something")
        # every state the page draws from here on, kept as it is drawn: the
        # fired button's busy state and the delivery words that follow it
        b.eval("""window.__seen = new Set(); (() => {
          const note = () => {
            const s = document.querySelector('#drawer.open #dsec-overview .dstatus');
            if (s) window.__seen.add('word:' + s.dataset.word);
            const p = s && s.querySelector('.dsacts [data-dslot]');
            if (p && p.classList.contains('busy')) window.__seen.add('busy');
            const w = (S.state.delivery || {})[%s];
            if (w) window.__seen.add('state:' + w.status.word);
          };
          window.__seer = new MutationObserver(note);
          window.__seer.observe(document.body, {subtree: true, childList: true,
                                                attributes: true});
          note(); })()""" % json.dumps(stem + ".md"))
        b.click(slot)
        self.assertTrue(b.wait("window.__posts.some(u => u.endsWith('/api/agent/act-pr'))", 10),
                        "the second click did not ask for ↻ act on PR")
        # the stub CLI refuses, so the run ends failed on the phase card;
        # the card stays in Review and its next step is the failed run's own
        deadline = time.time() + 60
        found = read(stem)
        while time.time() < deadline and found["status"]["word"] != "run-failed":
            time.sleep(0.5)
            found = read(stem)
        self.assertEqual("run-failed", found["status"]["word"], found["status"])
        self.assertEqual("read-failure", found["primary"]["action"])
        self.assertEqual("review", found["stage"])
        self.wait_word(stem, "run-failed")
        seen = set(b.eval("Array.from(window.__seen)"))
        b.eval("window.__seer && window.__seer.disconnect()")
        # running: the fired action locked as working while the run was
        # asked for, and — where the page read the board mid-run — the
        # agent's own word
        self.assertTrue("busy" in seen or "state:agent-working" in seen
                        or "word:agent-working" in seen,
                        f"the page never drew the repair running: {sorted(seen)}")
        self.assertFalse(b.eval("document.documentElement.scrollWidth > innerWidth + 1"),
                         "the page scrolls sideways")
        shot(f"phase-repair-handoff-{width}-{theme}")
        b.key("Escape")
        backend = S["backend"]
        refs = bv._git(backend.app, "branch", "--list", f"task/{stem}").stdout.strip()
        self.assertFalse(refs, "a task branch was cut for the phase")
        self.assertTrue((backend.work / "tasks" / "review" / f"{stem}.md").exists())
        listed = sorted(p.name for p in (backend.work / "tasks" / "backlog").iterdir())
        self.assertFalse([n for n in listed if "phase-repair" in n],
                         "a corrective card was written")
