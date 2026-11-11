"""One delivery status across the card face, the Overview and the merge
dialog, in a real browser (task 99, design #94 §15.3 B1–B5).

Every card here is a disposable one written into the fixture's own work
state, with its branch cut in the fixture's own application repository and
its evidence produced by the backend's real `/api/verify` running a stub
check. Nothing reaches a remote: `gh` is the fixture's refusing stub, and
the one pull request "merged on GitHub" is a snapshot the poll would have
written. Everything is put back afterwards. Run through verify/browser.

- **B1** — F1, F3, F7, F9, F13, F16, F18, F23 and F30: the face pill, the
  Overview's status and the dialog's status read the backend's word, and
  the Overview and the dialog offer the backend's primary action.
- **B2** — 390 px and 1280 px: the status heading, then the primary action
  (full width when narrow), then **More actions**; no sideways scroll.
- **B3** — the keyboard: Enter opens the card, Tab reaches the primary
  action, Enter arms a costly one without firing it, Escape closes the
  dialog and the detail view and gives focus back.
- **B4** — an in-progress card whose required check has not run wears no
  alarm and no ⚖ on its face; a current failure on one does.
- **B5** — opening every surface writes nothing (state, work state and
  application refs hashed before and after), and a live update keeps the
  open folds, the scroll position and a focused action.
- **The rows follow the server** — with the Overview open, the merge rule
  moves from one passed check to another (same word, same counts); after
  a live update, with no page reload, the Overview's rows name the new
  check and a merge dialog opened then draws the same rows.
"""

import hashlib
import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
DIALOG = "#sheetwrap .sheet"
FACE_WORDS = {
    "merging", "not-published", "run-failed", "agent-working", "phase-running",
    "returned", "cannot-assess", "rules-invalid", "check-failed", "cannot-run-check",
    "changes-requested", "checks-running", "checks-needed", "review-needed",
    "signoff-needed", "ready-to-merge", "ready-overridden", "merged-elsewhere",
    "waiting-for-phase",
}
PR = "https://github.com/example/fixture/pull/{n}"

# stem → (stage, the fixture row, what its branch holds, ask for the check?)
#   branch: None (no branch), "pass", "fail"
CARDS = {
    "61-delivery-f01": ("backlog", "F1", None, False),
    "62-delivery-f03": ("in-progress", "F3", None, False),
    "63-delivery-f07": ("review", "F7", "pass", False),
    "64-delivery-f09": ("review", "F9", "fail", True),
    "65-delivery-f13": ("review", "F13", "pass", True),
    "66-delivery-f16": ("review", "F16", "pass", True),
    "67-delivery-f18": ("review", "F18", "pass", False),
    "68-delivery-f23": ("review", "F23", "pass", True),   # → backlog, branch gone
    "69-delivery-b4-ahead": ("in-progress", "B4", "pass", False),
    "70-delivery-b4-failed": ("in-progress", "B4", "fail", True),
}
EXPECT = {
    "61-delivery-f01": ("not-started", "move"),
    "62-delivery-f03": ("ready-to-start", "start"),
    "63-delivery-f07": ("checks-needed", "run-required-checks"),
    "64-delivery-f09": ("check-failed", "act-pr"),
    "65-delivery-f13": ("review-needed", "review-pr"),
    "66-delivery-f16": ("ready-to-merge", "complete"),
    "67-delivery-f18": ("merged-elsewhere", "complete"),
    "68-delivery-f23": ("cannot-assess", "move"),
    "69-delivery-b4-ahead": ("in-progress", "start"),
    "70-delivery-b4-failed": ("check-failed", "start"),
}
STATUS = {"backlog": "Backlog", "in-progress": "In Progress", "review": "Review"}


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


def load(width=1280, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    return b


def card_sel(stem: str) -> str:
    return f'#board article.card[data-file="{stem}.md"]'


def wait_for_word(stem: str, word: str):
    """The board's compact reading is memoised in a time bucket; wait until
    the page holds the word the backend's full reading gives."""
    b = S["browser"]
    js = (f"(S.state.delivery || {{}})[{json.dumps(stem + '.md')}] && "
          f"S.state.delivery[{json.dumps(stem + '.md')}].status.word === {json.dumps(word)}")
    deadline = time.time() + 40
    while time.time() < deadline:
        if b.wait(js, 4):
            return
        b.eval("loadState()")
    raise bv.CheckFailed(f"{stem}: the page never read {word!r}; it holds "
                         f"{b.eval('JSON.stringify((S.state.delivery || {})[' + json.dumps(stem + '.md') + '])')}")


def open_detail(stem: str):
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


def close_detail():
    b = S["browser"]
    b.key("Escape")
    if not b.wait("!document.querySelector('#drawer').classList.contains('open')", 5):
        raise bv.CheckFailed("Escape did not close the detail view")


def drawn(where: str) -> dict:
    """The status block in `where`, read as a person would: its word, its
    heading, the primary action's text and whether it is disabled."""
    return S["browser"].eval(f"""(() => {{
      const s = document.querySelector('{where} .dstatus');
      if (!s) return null;
      const p = s.querySelector('.dsacts [data-dslot]');
      return {{ word: s.dataset.word, heading: s.querySelector('h3').innerText,
               primary: p ? p.innerText.trim() : null,
               disabled: p ? !!p.disabled : null,
               more: !!s.querySelector('details.dsmore') }}; }})()""")


def state_digest() -> str:
    """Every byte the surfaces could write: the board's state records, the
    work state's task files and the application's refs."""
    backend = S["backend"]
    h = hashlib.sha256()
    roots = [backend.tool / "manager" / "local" / "state" / sub
             for sub in ("gates", "evidence", "plans", "completions", "reviews")]
    roots += [backend.work / "tasks", backend.app / ".git" / "refs"]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            h.update(str(path).encode())
            h.update(path.read_bytes())
    return h.hexdigest()


class OneStatusEverywhere(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.policy = backend.tool / "manager" / "local" / "profiles.json"
        cls.saved = cls.policy.read_bytes() if cls.policy.exists() else None
        check = backend.base / "delivery-check.py"
        # passes unless the checkout it runs in holds a file named FAIL
        check.write_text("import os, sys\nsys.exit(1 if os.path.exists('FAIL') else 0)\n",
                         encoding="utf-8")
        cls.document = {
            "v": 1,
            "checks": [{"name": "unit", "run": f"{backend.python} {check}",
                        "root": "source"}],
            "profiles": {"work": {"verify": ["unit"]}, "review": {"verify": ["unit"]}},
            "gates": {"acceptance": {"checks": ["unit"], "review": "bench"}},
        }
        cls.policy.write_text(json.dumps(cls.document), encoding="utf-8")
        cls.files, cls.branches = [], []
        gates_dir = backend.tool / "manager" / "local" / "state" / "gates"
        gates_dir.mkdir(parents=True, exist_ok=True)
        cls.heads = {}
        for n, (stem, (stage, row, branch, ask)) in enumerate(CARDS.items()):
            card = backend.work / "tasks" / stage / f"{stem}.md"
            pr = f"**PR:** {PR.format(n=n + 1)}\n" if stage == "review" else ""
            card.write_text(f"# Delivery fixture {row}\n\n**Status:** {STATUS[stage]}\n"
                            f"**Priority:** Low\n{pr}\nA disposable card for the "
                            f"delivery status case ({row}).\n", encoding="utf-8")
            cls.files.append(card)
            if branch:
                ref = f"task/{stem}"
                backend._git(backend.app, "checkout", "-q", "-b", ref)
                if branch == "fail":
                    (backend.app / "FAIL").write_text("make the check fail\n")
                    backend._git(backend.app, "add", "FAIL")
                backend._git(backend.app, "commit", "-q", "--allow-empty", "-m",
                             f"fixture: {row} implementation")
                backend._git(backend.app, "checkout", "-q", "main")
                cls.branches.append(ref)
        # evidence, through the backend's own door
        for stem, (stage, row, branch, ask) in CARDS.items():
            if not ask:
                continue
            status, body = backend.post("/api/verify", {
                "file": f"{stem}.md", "stage": stage, "check": "unit"}, timeout=120)
            if status != 200:
                raise bv.SetupError(f"{row}: /api/verify answered {status}: {body[:300]!r}")
        for stem in CARDS:
            cls.heads[stem] = (read(stem).get("candidate") or {}).get("commit")
        # F16 and F30: Bench approved this exact head
        f16 = "66-delivery-f16"
        (gates_dir / f"{f16}.verdicts.json").write_text(json.dumps([{
            "v": 1, "task": f"{f16}.md", "verdict": "APPROVE", "head": cls.heads[f16],
            "agent": None, "actor": "agent", "reviewId": None, "note": "fixture",
            "recorded": time.time(), "published": None}]), encoding="utf-8")
        # F18: the poll saw the PR merged on GitHub at the branch's head
        f18 = "67-delivery-f18"
        (gates_dir / f"{f18}.pr.json").write_text(json.dumps({
            "v": 1, "task": f"{f18}.md", "head": cls.heads[f18], "prState": "MERGED",
            "merged": True, "url": PR.format(n=7), "observed": time.time()}),
            encoding="utf-8")
        # F23: returned to the backlog, its branch gone, its evidence kept
        f23 = "68-delivery-f23"
        moved = backend.work / "tasks" / "backlog" / f"{f23}.md"
        src = backend.work / "tasks" / "review" / f"{f23}.md"
        moved.write_text(src.read_text(encoding="utf-8").replace(
            "**Status:** Review", "**Status:** Backlog"), encoding="utf-8")
        src.unlink()
        cls.files.append(moved)
        backend._git(backend.app, "branch", "-D", f"task/{f23}")
        cls.branches.remove(f"task/{f23}")
        # every card's text changed after its records did, so no memoised
        # reading predates them
        for card in cls.files:
            if card.exists():
                card.write_text(card.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        for card in cls.files:
            card.unlink(missing_ok=True)
        for ref in cls.branches:
            try:
                backend._git(backend.app, "branch", "-D", ref)
            except bv.SetupError:
                pass
        if cls.saved is None:
            cls.policy.unlink(missing_ok=True)
        else:
            cls.policy.write_bytes(cls.saved)

    def test_1_b1_every_surface_reads_the_backends_word_and_action(self):
        b = load()
        seen = {}
        for stem, (word, action) in EXPECT.items():
            with self.subTest(stem):
                found = read(stem)
                self.assertEqual(word, found["status"]["word"], found["status"])
                self.assertEqual(action, (found["primary"] or {}).get("action"), found["primary"])
                wait_for_word(stem, word)
                face = b.eval(f"(() => {{ const p = document.querySelector('{card_sel(stem)} .pill.status');"
                              " return p ? p.dataset.word || p.innerText : null; })()")
                self.assertEqual(word if word in FACE_WORDS else None, face, f"{stem}: the face")
                open_detail(stem)
                ov = drawn("#dsec-overview")
                self.assertEqual(word, ov["word"], f"{stem}: the Overview")
                if found["primary"]:
                    self.assertIn(found["primary"]["label"].split(" ")[-1], ov["primary"])
                    self.assertEqual(not found["primary"]["enabled"], ov["disabled"])
                head = b.eval("document.querySelector('#drawer .dhead .pill').dataset.word")
                self.assertEqual(word, head, f"{stem}: the detail head")
                close_detail()
                if found["counts"]["required"]:
                    b.eval(f"gateSheet(findTask({json.dumps(stem + '.md')}))")
                    if not b.wait(f"!!document.querySelector('{DIALOG} .dstatus')", 20):
                        raise bv.CheckFailed(f"{stem}: the dialog drew no status")
                    dlg = drawn(DIALOG)
                    self.assertEqual(word, dlg["word"], f"{stem}: the dialog")
                    self.assertEqual(ov["primary"], dlg["primary"], f"{stem}: the dialog's action")
                    b.key("Escape")
                    b.wait("!document.querySelector('#sheetwrap').classList.contains('open')", 5)
                seen[stem] = {"word": word, "face": face, "overview": ov["word"]}
        S["metrics"]["deliveryB1"] = seen

    def test_2_f30_unrelated_context_keeps_the_pass(self):
        """F30, the SAFER36 shape: approved context for the next card is
        added to the policy; the commit and the check did not change, so
        the card is still ready to merge — with no rerun."""
        backend = S["backend"]
        stem = "66-delivery-f16"
        runs = len(list((backend.tool / "manager" / "local" / "state" / "evidence").glob("*.json")))
        (backend.work / "reference").mkdir(exist_ok=True)
        mock = backend.work / "reference" / "card37-mockup.md"
        mock.write_text("the next card's mockup\n", encoding="utf-8")
        self.addCleanup(mock.unlink, True)
        doc = dict(self.document, context=[{"name": "card37-mockup",
                                            "path": "work:reference/card37-mockup.md",
                                            "required": False}])
        self.policy.write_text(json.dumps(doc), encoding="utf-8")
        self.addCleanup(self.policy.write_text, json.dumps(self.document), encoding="utf-8")
        found = read(stem)
        self.assertEqual("ready-to-merge", found["status"]["word"], found)
        self.assertEqual({"required": 2, "met": 2}, found["counts"])
        b = load()
        wait_for_word(stem, "ready-to-merge")
        open_detail(stem)
        self.assertEqual("ready-to-merge", drawn("#dsec-overview")["word"])
        shot("delivery-f30-ready-1280x900")
        close_detail()
        self.assertEqual(runs, len(list((backend.tool / "manager" / "local" / "state" /
                                         "evidence").glob("*.json"))),
                         "an unrelated policy edit caused a rerun")

    def test_3_b2_narrow_and_wide_layouts(self):
        for width, height in ((390, 844), (1280, 900)):
            for stem in ("63-delivery-f07", "66-delivery-f16"):
                with self.subTest(f"{stem} at {width}"):
                    b = load(width, height)
                    open_detail(stem)
                    m = b.eval("""(() => {
                      const s = document.querySelector('#dsec-overview .dstatus');
                      const h = s.querySelector('h3').getBoundingClientRect();
                      const p = s.querySelector('.dsacts [data-dslot]');
                      const pb = p ? p.getBoundingClientRect() : null;
                      const more = s.querySelector('details.dsmore');
                      const body = document.querySelector('#drawerbody');
                      return { headTop: h.top, primTop: pb && pb.top, primWidth: pb && pb.width,
                               blockWidth: s.getBoundingClientRect().width,
                               moreTop: more ? more.getBoundingClientRect().top : null,
                               over: body.scrollWidth - body.clientWidth,
                               docOver: document.documentElement.scrollWidth - innerWidth }; })()""")
                    shot(f"delivery-{stem}-{width}x{height}")
                    self.assertLessEqual(m["over"], 1, f"the detail view scrolls sideways at {width}")
                    self.assertLessEqual(m["docOver"], 1, f"the page scrolls sideways at {width}")
                    self.assertIsNotNone(m["primTop"], "no primary action drawn")
                    self.assertLess(m["headTop"], m["primTop"], "the status is not above its action")
                    if m["moreTop"] is not None:
                        self.assertLess(m["primTop"], m["moreTop"],
                                        "More actions is not after the primary")
                    if width == 390:
                        # full width, less the block's own padding and border
                        self.assertGreater(m["primWidth"], m["blockWidth"] - 40,
                                           "the primary action is not full width at 390px")
                    S["metrics"][f"deliveryB2-{stem}-{width}"] = m
                    close_detail()

    def test_4_b3_keyboard_arms_without_firing_and_escape_returns_focus(self):
        stem = "65-delivery-f13"          # ◔ review PR: costly, so armed
        b = load()
        sel = card_sel(stem)
        open_detail(stem)
        # from the section row, the next stop is the status's own action:
        # the section is not itself a tab stop, and nothing sits between
        b.eval("document.querySelector('#dtab-overview').focus()")
        for _ in range(12):
            if b.eval("!!(document.activeElement && document.activeElement.dataset"
                      " && document.activeElement.dataset.dslot === 'ov-p')"):
                break
            b.key("Tab")
        self.assertTrue(b.eval("document.activeElement.dataset.dslot === 'ov-p'"),
                        "Tab never reached the Overview's primary action")
        b.key("Enter")
        self.assertTrue(b.wait("document.activeElement.classList.contains('armed')", 5),
                        "Enter did not arm the primary action")
        armed = b.eval("document.activeElement.innerText")
        shot("delivery-b3-armed-1280x900")
        # let the window lapse: arming fires nothing
        time.sleep(6)
        status, body = S["backend"].get("/api/state")
        agents = json.loads(body).get("agents") or []
        self.assertFalse([a for a in agents if a.get("task") == f"{stem}.md"],
                         "arming the action launched something")
        # the dialog: Escape closes it and focus goes back to what opened it
        b.eval("document.querySelector('#dsec-overview .dsacts [data-dslot]').focus()")
        b.eval(f"gateSheet(findTask({json.dumps(stem + '.md')}))")
        if not b.wait(f"!!document.querySelector('{DIALOG} .dstatus')", 20):
            raise bv.CheckFailed("the dialog drew no status")
        b.key("Escape")
        self.assertTrue(b.wait("!document.querySelector('#sheetwrap').classList.contains('open')"
                               " && document.activeElement && document.activeElement.dataset"
                               " && document.activeElement.dataset.dslot === 'ov-p'", 5),
                        "Escape did not give focus back to the action that opened the dialog")
        # the detail view: Escape closes it and focus goes back to the card
        b.key("Escape")
        self.assertTrue(b.wait(f"document.activeElement === document.querySelector('{sel}')", 5),
                        "Escape did not give focus back to the card")
        S["metrics"]["deliveryB3"] = {"armedLabel": armed}

    def test_5_b4_future_requirements_are_not_alarms(self):
        b = load()
        ahead, failed = "69-delivery-b4-ahead", "70-delivery-b4-failed"
        wait_for_word(ahead, "in-progress")
        wait_for_word(failed, "check-failed")
        face = lambda stem: b.eval(f"""(() => {{
          const c = document.querySelector('{card_sel(stem)}');
          const chips = [...c.querySelectorAll('.chiprow .chip2')].map(x => x.innerText);
          const pill = c.querySelector('.pill.status');
          return {{ pill: pill ? (pill.dataset.word || pill.innerText) : null,
                   bad: c.classList.contains('verdict-bad'),
                   gateOnFace: !!c.querySelector('.chiprow [data-gate]'),
                   evidenceOnFace: !!c.querySelector('.chiprow [data-verify]'), chips }}; }})()""")
        a, f = face(ahead), face(failed)
        shot("delivery-b4-board-1280x900")
        self.assertIsNone(a["pill"], f"a check that lies ahead put a pill on the face: {a}")
        self.assertFalse(a["bad"], "a check that lies ahead is drawn as an alarm")
        self.assertFalse(a["gateOnFace"], "an unmet requirement that lies ahead is on the face")
        self.assertEqual("check-failed", f["pill"], f)
        self.assertTrue(f["bad"], "a current failure is not drawn as an alarm")
        self.assertTrue(f["evidenceOnFace"], "a current failure's evidence left the face")
        S["metrics"]["deliveryB4"] = {"ahead": a, "failed": f}

    def test_6_b5_reading_writes_nothing_and_a_live_update_keeps_the_place(self):
        stem = "66-delivery-f16"
        before = state_digest()
        b = load()
        wait_for_word(stem, "ready-to-merge")
        open_detail(stem)
        b.eval(f"gateSheet(findTask({json.dumps(stem + '.md')}))")
        b.wait(f"!!document.querySelector('{DIALOG} .dstatus')", 20)
        b.key("Escape")
        b.wait("!document.querySelector('#sheetwrap').classList.contains('open')", 5)
        time.sleep(1)
        self.assertEqual(before, state_digest(), "opening the surfaces wrote something")

        # open the folds, focus the primary, scroll — then a live update
        b.eval("""(() => {
          for (const d of document.querySelectorAll('#dsec-overview details.dreqfold')) {
            d.open = true; d.dispatchEvent(new Event('toggle')); }
          document.querySelector('#dsec-overview .dsacts [data-dslot]').focus(); })()""")
        b.eval("document.querySelector('#drawerbody').scrollTop = 120;"
               "document.querySelector('#drawerbody').dispatchEvent(new Event('scroll'))")
        kept = b.eval("document.querySelector('#drawerbody').scrollTop")
        folds = b.eval("document.querySelectorAll('#dsec-overview details.dreqfold[open]').length")
        self.assertGreater(folds, 0, "no fold to keep open")
        extra = S["backend"].work / "tasks" / "backlog" / "71-delivery-live-update.md"
        extra.write_text("# A live update\n\n**Status:** Backlog\n**Priority:** Low\n\n"
                         "Arrives while the detail view is open.\n", encoding="utf-8")
        self.addCleanup(extra.unlink, True)
        if not b.wait("!!findTask('71-delivery-live-update.md')", 20):
            raise bv.CheckFailed("the live update never arrived")
        b.wait("document.querySelector('#drawer.open #dsec-overview .dstatus')", 5)
        self.assertEqual(folds, b.eval(
            "document.querySelectorAll('#dsec-overview details.dreqfold[open]').length"),
            "a live update closed a fold")
        self.assertEqual("ov-p", b.eval("(document.activeElement.dataset || {}).dslot"),
                         "a live update dropped the focused action")
        self.assertLessEqual(abs(b.eval("document.querySelector('#drawerbody').scrollTop") - kept), 4,
                             "a live update moved the reader")
        shot("delivery-b5-after-live-update-1280x900")
        close_detail()

    def test_7_a_rule_replaced_with_the_same_totals_reaches_the_open_overview(self):
        """The supervisor's finding at 4618dd5. F16's rule moves from `unit`
        to `lint` — both passed at the head, so the word, the counts and
        the classes are unchanged — while the Overview is open. After the
        live update, without reloading the page, the Overview's rows name
        `lint`, and a merge dialog opened then names the same rows."""
        backend = S["backend"]
        stem = "66-delivery-f16"
        doc = json.loads(json.dumps(self.document))
        doc["checks"].append(dict(doc["checks"][0], name="lint"))
        for intent in doc["profiles"].values():
            intent["verify"] = ["unit", "lint"]
        self.policy.write_text(json.dumps(doc), encoding="utf-8")
        self.addCleanup(self.policy.write_text, json.dumps(self.document), encoding="utf-8")
        status, body = backend.post("/api/verify", {
            "file": f"{stem}.md", "stage": "review", "check": "lint"}, timeout=120)
        if status != 200:
            raise bv.SetupError(f"lint: /api/verify answered {status}: {body[:300]!r}")
        before = read(stem)
        self.assertEqual("ready-to-merge", before["status"]["word"], before["status"])

        rows = lambda where: S["browser"].eval(
            f"[...document.querySelectorAll('{where} .dreqlist .name')].map(n => n.innerText)")
        b = load()
        wait_for_word(stem, "ready-to-merge")
        open_detail(stem)
        if not b.wait("[...document.querySelectorAll('#dsec-overview .dreqlist .name')]"
                      ".some(n => n.innerText.startsWith('unit'))", 20):
            raise bv.CheckFailed(f"the Overview never drew the unit row: {rows('#dsec-overview')}")
        reloads = b.eval("performance.getEntriesByType('navigation').length")
        b.eval("window.__sameDocument = true")

        doc["gates"]["acceptance"]["checks"] = ["lint"]
        self.policy.write_text(json.dumps(doc), encoding="utf-8")
        after = read(stem)
        for key in ("status", "counts", "history"):
            self.assertEqual(before[key], after[key], f"the fixture changed {key}")
        self.assertNotEqual(before["detail"], after["detail"])
        # a live update, over the page's own event stream
        extra = backend.work / "tasks" / "backlog" / "72-delivery-rule-moved.md"
        extra.write_text("# The rule moved\n\n**Status:** Backlog\n**Priority:** Low\n\n"
                         "Arrives while the Overview is open.\n", encoding="utf-8")
        self.addCleanup(extra.unlink, True)
        if not b.wait("!!findTask('72-delivery-rule-moved.md')", 20):
            raise bv.CheckFailed("the live update never arrived")
        js = (f"(S.state.delivery[{json.dumps(stem + '.md')}] || {{}}).detail === "
              f"{json.dumps(after['detail'])}")
        deadline = time.time() + 40
        while not b.wait(js, 4):
            if time.time() > deadline:
                raise bv.CheckFailed("the page never read the moved rule's reading")
            # the compact reading is memoised in a time bucket; the next
            # live update carries it
            extra.write_text(extra.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        if not b.wait("(() => { const n = [...document.querySelectorAll("
                      "'#dsec-overview .dreqlist .name')].map(n => n.innerText);"
                      " return n.some(x => x.startsWith('lint')) && !n.some(x => x.startsWith('unit')); })()",
                      20):
            raise bv.CheckFailed(f"the open Overview kept the old rows: {rows('#dsec-overview')}")
        self.assertTrue(b.eval("window.__sameDocument === true"), "the page was reloaded")
        self.assertEqual(reloads, b.eval("performance.getEntriesByType('navigation').length"))
        overview = rows("#dsec-overview")
        b.eval(f"gateSheet(findTask({json.dumps(stem + '.md')}))")
        if not b.wait(f"!!document.querySelector('{DIALOG} .dreqlist')", 20):
            raise bv.CheckFailed("the dialog drew no requirement rows")
        dialog = rows(DIALOG)
        shot("delivery-rule-moved-dialog-1280x900")
        self.assertEqual(overview, dialog, "the Overview and the dialog disagree")
        self.assertEqual(["lint"], [r["check"] for r in after["requirements"]
                                    if r["kind"] == "check"])
        b.key("Escape")
        b.wait("!document.querySelector('#sheetwrap').classList.contains('open')", 5)
        close_detail()
        S["metrics"]["deliveryRuleMoved"] = {"overview": overview, "dialog": dialog}


if __name__ == "__main__":
    unittest.main()
