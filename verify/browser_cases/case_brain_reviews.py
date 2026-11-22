"""Reviews filed on cards, read in the Brain, in the real browser (task 168).

A disposable backend of this case's own holds cards with filed reviews: a
card in review/ with two PR review rounds (each with the provenance bench
records at filing: the full head and base and who said so) and a relevance
review bound to the brief it was launched with; an archived card with a
legacy relevance review; and two cards sharing one number. The case drives
the page with the pointer and the keyboard and takes screenshots to look
at:

- **the library**: the reviews appear under kind Review, Unclassified,
  found by search (a reviewer's name), with no file written to say so;
- **a review's page** at `/brain/r/<card>/<review>`: the card's own report
  (its hostile markup inert), its provenance — the full head, the base and
  their authority, the brief revision assessed — and its assessment, in
  Night and Daylight at 1280px and 390px, with nothing overflowing;
- **both ways**: Open on the card lands on that review in the card's
  Reviews, unfolded and focused; its Read in the Brain link (by keyboard)
  returns to the page; Back walks the history;
- **classification**: Classify writes one record under brain/reviews/ and
  the card is byte for byte unchanged;
- **honest absences**: a card not on this board, a review the card does
  not hold and a number two cards share are each said, never swapped for
  another review.
"""

import hashlib
import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION

HEAD = "0123456789abcdef0123456789abcdef01234567"
BASE = "fedcba9876543210fedcba9876543210fedcba98"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def rid(prefix: str, heading: str) -> str:
    return prefix + hashlib.sha256(heading.strip().encode("utf-8")).hexdigest()[:10]


def marker(meta: dict) -> str:
    return "<!-- bench:review " + json.dumps({**meta, "v": 1}, sort_keys=True) + " -->"


def filed(heading: str, report: str, meta: dict | None = None) -> str:
    # as agents._file_report writes it: the digest of exactly the text filed
    sha = hashlib.sha256(report.encode("utf-8")).hexdigest()[:12]
    mark = marker({**meta, "reportSha": sha}) + "\n\n" if meta else ""
    return f"\n\n---\n\n{heading}\n\n{mark}{report}\n"


def brief_sha(brief: str) -> str:
    return hashlib.sha256(brief.strip().encode("utf-8")).hexdigest()


R1 = "## PR review at 0123456789ab — 2026-11-20 10:00 (Juno)"
R2 = "## PR review at 0123456789ab — 2026-11-21 11:00 (Juno)"
REL = "## Relevance review — 2026-11-21 12:00 (Hazel)"
OLD = "## Relevance review — 2026-11-01"
TWIN = "## Relevance review — 2026-11-02 08:00 (Fern)"
BRIEF = "Route each review report to the card its run was launched for."
PR = {"kind": "pr", "head": {"ref": "task/7-routing", "commit": HEAD, "authority": "github"},
      "base": {"ref": "main", "commit": BASE, "authority": "github"},
      "pr": "https://github.com/o/r/pull/3", "prNumber": "3", "prState": "OPEN",
      "label": "current", "model": "opus-fixture", "reviewer": "Juno"}
CARD7 = (
    "# 07 — Route review reports\n\n**Status:** Review\n**Priority:** High\n\n" + BRIEF + "\n"
    + filed(R1, "PR REVIEW: REQUEST CHANGES\n\nThe first round found the routing bug.\n\n"
            "Hostile markup stays text: <script>window.__reviewPwned = 1</script> "
            "<img src=x onerror=\"window.__reviewPwned = 2\">", PR)
    + filed(R2, "PR REVIEW: APPROVE\n\nThe second round confirms the fix landed.", PR)
    + filed(REL, "RELEVANCE REVIEW: Still relevant\n\nThe card still describes needed work.",
            {"kind": "relevance", "briefRevision": brief_sha(BRIEF), "briefSource": "launch",
             "model": "opus-fixture", "reviewer": "Hazel"}))
CARD9 = ("# 09 — An old card\n\n**Status:** Done\n**Priority:** Low\n\nOld.\n"
         + filed(OLD, "RELEVANCE REVIEW: Already done\n\nLegacy: nothing recorded."))
TWIN_A = ("# 12 — One twin\n\n**Status:** To Do\n**Priority:** Low\n\nA.\n"
          + filed(TWIN, "RELEVANCE REVIEW: Partly done\n\nCopied by hand."))
TWIN_B = TWIN_A.replace("One twin", "Other twin")
ENTRY = ("# How reports are routed\n\n**Brain:** 1\n**Id:** e-routing\n**Kind:** Guide\n"
         "**Classification:** Current\n**Summary:** Where a report is filed.\n"
         f"**Source:** #7/{rid('rv-', R2)}\n\nThe routing, explained.\n")


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


class BrainReviews(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-reviews-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        tasks = cls.backend.work / "tasks"
        cls.card7 = tasks / "review" / "07-route-review-reports.md"
        cls.card7.write_text(CARD7, encoding="utf-8")
        (tasks / "archive" / "09-an-old-card.md").write_text(CARD9, encoding="utf-8")
        (tasks / "to-do" / "12-one-twin.md").write_text(TWIN_A, encoding="utf-8")
        (tasks / "backlog" / "12-other-twin.md").write_text(TWIN_B, encoding="utf-8")
        (cls.backend.work / "brain").mkdir()
        (cls.backend.work / "brain" / "routing.md").write_text(ENTRY, encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.before = tree(cls.backend.work)
        cls.r1, cls.r2, cls.rel = rid("rv-", R1), rid("rv-", R2), rid("rl-", REL)

    @classmethod
    def tearDownClass(cls):
        b = S["browser"]
        try:
            b.eval("localStorage.removeItem('bench-theme')")
        except (bv.CheckFailed, bv.SetupError):
            pass
        b.origin = cls.origin
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    def open(self, path, width=1280, height=860, dark=True, ready="#bshell .b-title"):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        b.goto(path)
        if not b.wait(f"!!document.querySelector({json.dumps(ready)})", 15):
            raise bv.CheckFailed(f"{path}: nothing drew: "
                                 + str(b.eval("document.body.innerText")[:600]))
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        return b

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    def test_1_reviews_are_listed_unclassified_and_searchable(self):
        for width, height, label in ((1280, 860, "desktop"), (390, 844, "narrow")):
            for dark in (True, False):
                b = self.open("/brain", width, height, dark)
                if not b.wait("!!document.querySelector('[data-bkindsec=\"Review\"] .b-list')", 10):
                    raise bv.CheckFailed("no Review section was drawn: " + self.text(b)[:600])
                section = self.text(b, '[data-bkindsec="Review"]')
                for word in ("PR review round 2 of #7", "PR review round 1 of #7",
                             "Relevance review of #7", "Relevance review of #9",
                             "Unclassified", "not yet classified"):
                    self.assertIn(word, section, f"{label}: {word}")
                self.no_overflow(b, f"list {label}")
                shot(f"brain-reviews-list-{label}-{'night' if dark else 'day'}")
        b = self.open("/brain")
        b.eval("(() => { const q = document.querySelector('[data-bfind=\"q\"]'); q.value = 'Hazel';"
               " q.dispatchEvent(new Event('input', {bubbles: true})); return true; })()")
        b.wait("document.querySelectorAll('.b-results .b-row').length === 1", 5)
        rows = b.eval("[...document.querySelectorAll('.b-results .b-row a')].map(a => a.textContent)")
        self.assertEqual(rows, ["Relevance review of #7 — 07 — Route review reports"])
        self.assertEqual(tree(self.backend.work), self.before, "listing wrote something")

    def test_2_a_review_page_shows_the_cards_report_and_its_provenance(self):
        b = self.open("/brain")
        b.click(f'.b-list a[data-brain-id="7/{self.r1}"]')
        if not b.wait(f"location.pathname === '/brain/r/7/{self.r1}' && !!document.querySelector('#btitle')", 10):
            raise bv.CheckFailed("the review never opened at its address")
        self.assertEqual(b.eval("document.activeElement && document.activeElement.id"), "btitle")
        page = self.text(b)
        for word in ("The first round found the routing bug.", HEAD, BASE, "said by github",
                     "opus-fixture", "Unclassified", "Open on the card", "not a formal GitHub approval",
                     f"/brain/r/7/{self.r1}", "Unknown", "the same text bench recorded filing",
                     "2 on this card"):
            self.assertIn(word, page, word)
        self.assertEqual(b.eval("typeof window.__reviewPwned"), "undefined")
        self.assertEqual(b.eval("document.querySelector('.b-prose').querySelectorAll('script, img, [onerror]').length"), 0)
        # there is nothing to edit the report with
        self.assertEqual(b.eval("document.querySelectorAll('#bshell [data-bact=\"edit\"], #bshell textarea').length"), 0)
        shot("brain-review-desktop-night")
        b.eval("S.dark = false; applyTheme(); true")
        shot("brain-review-desktop-day")
        b.viewport(390, 844)
        time.sleep(0.3)
        self.no_overflow(b, "review narrow")
        shot("brain-review-narrow-day")
        b.eval("S.dark = true; applyTheme(); true")
        shot("brain-review-narrow-night")
        b = self.open(f"/brain/r/7/{self.rel}")
        page = self.text(b)
        self.assertIn(brief_sha(BRIEF)[:12], page)
        self.assertIn("the revision the run was launched with", page)
        self.assertIn("Current — the brief is the revision it assessed", page)
        self.assertIn("How reports are routed", self.text(b, ".b-side"),
                      "an entry citing the card is listed as citing it")
        b = self.open("/brain/r/9/" + rid("rl-", OLD))
        page = self.text(b)
        self.assertIn("Card archived", page)
        self.assertIn("unknown — the revision this review read was not recorded", page)
        shot("brain-review-legacy-archived")

    def test_3_to_the_card_and_back_by_pointer_and_keyboard(self):
        b = self.open(f"/brain/r/7/{self.r1}")
        b.click("#bshell a.b-oncard")
        if not b.wait(f"location.pathname === '/cards/7' && "
                      f"document.activeElement?.id === 'rvs-{self.r1}'", 15):
            raise bv.CheckFailed("Open on the card did not land on the review: "
                                 + str(b.eval("[location.href, document.activeElement?.id]")))
        self.assertEqual(b.eval("location.search"), "?tab=reviews")
        self.assertTrue(b.eval(f"document.getElementById('rvw-{self.r1}').open"))
        shot("brain-review-on-card")
        link = f'#rvw-{self.r1} a[data-brain-review="{self.r1}"]'
        b.eval(f"document.querySelector({json.dumps(link)}).focus(); true")
        b.key("Enter")
        if not b.wait(f"location.pathname === '/brain/r/7/{self.r1}' && !!document.querySelector('#btitle')", 10):
            raise bv.CheckFailed("Read in the Brain did not return to the review")
        b.eval("history.back()")
        if not b.wait("location.pathname === '/cards/7'", 10):
            raise bv.CheckFailed("Back did not return to the card")
        b.eval("history.back()")
        if not b.wait(f"location.pathname === '/brain/r/7/{self.r1}'", 10):
            raise bv.CheckFailed("Back again did not return to the review")

    def test_4_the_keyboard_reaches_a_review_from_the_list(self):
        b = self.open("/brain")
        b.eval("(() => { const k = document.querySelector('[data-bkind=\"Review\"]'); k.focus(); return true; })()")
        b.key("Enter")
        b.wait("!!document.querySelector('[data-bkindsec=\"Review\"]') && "
               "!document.querySelector('[data-bkindsec=\"Guide\"]')", 5)
        for _ in range(30):
            b.key("Tab")
            if b.eval("(document.activeElement?.dataset?.brainId || '').startsWith('7/')"):
                break
        target = b.eval("document.activeElement?.dataset?.brainId || null")
        self.assertTrue(target and target.startswith("7/"), f"Tab never reached a review: {target}")
        ring = b.eval("getComputedStyle(document.activeElement).outlineStyle")
        self.assertNotEqual(ring, "none", "the focused review link shows no focus ring")
        b.key("Enter")
        if not b.wait(f"location.pathname === '/brain/r/{target}'", 10):
            raise bv.CheckFailed("Enter on a focused review did not open it")

    def test_5_absences_are_said_and_nothing_else_is_shown(self):
        b = self.open("/brain/r/999/rv-0000000000")
        self.assertIn("No card 999 on this board", self.text(b))
        b = self.open("/brain/r/7/rv-0000000000")
        page = self.text(b)
        self.assertIn("No review rv-0000000000 on card 7", page)
        self.assertNotIn("first round", page)
        b = self.open("/brain/r/12/" + rid("rl-", TWIN))
        page = self.text(b)
        self.assertIn("2 cards numbered 12 hold review", page)
        self.assertIn("12-one-twin.md", page)
        self.assertIn("12-other-twin.md", page)
        shot("brain-review-ambiguous")
        self.assertEqual(tree(self.backend.work), self.before, "browsing wrote something")

    def test_6_classify_writes_one_record_and_leaves_the_card(self):
        card = self.card7.read_bytes()
        b = self.open(f"/brain/r/7/{self.r2}")
        b.click('#bshell [data-bact="rv-classify"]')
        b.wait("!!document.querySelector('[data-rvf=\"reason\"]')", 5)
        b.eval("(() => { const s = document.querySelector('[data-rvf=\"classification\"]'); s.value = 'Current';"
               " s.dispatchEvent(new Event('change', {bubbles: true}));"
               " const r = document.querySelector('[data-rvf=\"reason\"]'); r.value = 'the approved routing';"
               " r.dispatchEvent(new Event('input', {bubbles: true})); return true; })()")
        shot("brain-review-classify-form")
        b.click('#bshell [data-bact="rv-save"]')
        if not b.wait("!document.querySelector('[data-rvf]') && "
                      "(document.querySelector('.b-side')?.innerText || '').includes('the approved routing')", 10):
            raise bv.CheckFailed("the classification was not shown: " + self.text(b)[:800])
        self.assertIn("Current", self.text(b, ".b-kicker"))
        record = self.backend.work / "brain" / "reviews" / f"c7-{self.r2}.md"
        self.assertTrue(record.is_file())
        self.assertNotIn("second round", record.read_text(encoding="utf-8"))
        self.assertEqual(self.card7.read_bytes(), card, "classifying changed the card")
        shot("brain-review-classified")
