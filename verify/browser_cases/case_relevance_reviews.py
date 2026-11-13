"""Relevance reviews in Reviews, with comments and refinement (task 84), in
the browser.

A disposable card shaped like card 79 — Hazel's relevance review and no PR
review — is opened from Activity's link into its exact review in Reviews,
beside a PR-review empty state; a "before" view rebuilds what the Reviews
section drew for the same card before this change, at the same viewport.
Then: a comment saved against the review (and still there after a
reload), a draft that survives a new review arriving by live update,
Update card from this review failing (the stub has no reply), retried,
proposing a diff and applied — with nothing else launched and the card in
its stage — then a second proposal discarded. Both themes, 390px, keyboard
reach and inert untrusted content.

No model is launched: the disposable backend's vendor CLI is a stub that
answers a refinement only with the reply a case wrote for it. The card is
written into the fixture's work state and removed afterwards.
"""

import json
import re
import time
import unittest
from pathlib import Path

import benchverify as bv

S = bv.SESSION
STEM = "11-relevance-review-fixture"
FILE = f"{STEM}.md"
FIXTURE = (Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "relevance"
           / "card-79.md").read_text(encoding="utf-8")
# the fixture's own acceptance record would need a matching revision; this
# case is about reviews, so the card keeps its criteria untracked
CARD = re.sub(r"\n## Acceptance record\n.*?\n---\n", "\n---\n", FIXTURE, flags=re.S) \
    .replace("<!-- ac:headline -->", "").replace("<!-- ac:untested -->", "")
LATER = ("\n---\n\n## Relevance review — 2026-11-12 09:30 (Wren)\n\n"
         "RELEVANCE REVIEW: Still relevant\n\nChecked again after the edit.\n")
REWORDED = """## Context

The Configuration check is long and its first screen does not say what to
do next.

## What to build

- Fold rows that passed apart from rows that were not tested.
- Hold the layout at 390px.

## Acceptance

- [ ] The headline names a specific result
- [ ] Untested rows never read as passed
"""
DRAWER = "document.querySelector('#drawerbody')"
REL = "#dsec-reviews details.rvrel"


def proposal(brief: str) -> str:
    return ("REFINEMENT PROPOSAL\n<<<EXPLANATION\nDropped the done headline bullet and "
            f"added the 390px layout, as the review recommends.\nEXPLANATION>>>\n"
            f"<<<BRIEF\n{brief}BRIEF>>>\n")


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


class RelevanceReviews(unittest.TestCase):

    def setUp(self):
        backend = S["backend"]
        self.card = backend.work / "tasks" / "to-do" / FILE
        self.card.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.card.unlink, True)
        self.addCleanup(backend.refine_reply.unlink, True)
        self.addCleanup(lambda: S["browser"].eval("localStorage.removeItem('bench-theme')"))
        code, body = backend.get(f"/api/card/acceptance?file={FILE}&stage=to-do")
        self.assertEqual(code, 200, body[:300])
        self.rid = json.loads(body)["relevance"][0]["id"]

    # — helpers —

    def text(self) -> str:
        return self.card.read_text(encoding="utf-8")

    def brief(self) -> str:
        return self.text().split("**Type:** Feature\n", 1)[1].split("\n---\n")[0]

    def discussion(self) -> dict:
        code, body = S["backend"].get(f"/api/card/discussion?file={FILE}&stage=to-do")
        self.assertEqual(code, 200, body[:300])
        return json.loads(body)

    def agents(self) -> list:
        code, body = S["backend"].get("/api/state")
        self.assertEqual(code, 200)
        return [a for a in json.loads(body)["agents"] if a.get("task") == FILE]

    def open_card(self, width=1280, height=900, dark=False):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        sel = f'#board article.card[data-file="{FILE}"]'
        self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 15),
                        "the disposable card never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .dtitle')", 5),
                        "the detail view did not open")
        self.assertTrue(b.wait("!!(S.acc[%s] || {}).data && !!(S.disc[%s] || {}).data"
                               % (json.dumps(FILE), json.dumps(FILE)), 10),
                        "the card's records never loaded")
        self.assertTrue(b.wait("document.querySelector('#drawer').getAnimations()"
                               ".every(a => a.playState === 'finished')", 5))

    def reviews(self):
        b = S["browser"]
        b.click("#dtab-reviews")
        self.assertTrue(b.wait(f"!document.querySelector('#dsec-reviews').hidden"
                               f" && !!document.querySelector('{REL}')", 5),
                        "Reviews did not draw the relevance review")

    def no_sideways_scroll(self, where: str):
        over = S["browser"].eval(f"{DRAWER}.scrollWidth - {DRAWER}.clientWidth")
        self.assertLessEqual(over, 1, f"the detail view scrolls sideways at {where}")

    def type_in(self, words: str, rid=None):
        S["browser"].eval(f"(() => {{ const t = document.getElementById('rvbox-{rid or self.rid}');"
                          f" t.focus(); t.value = {json.dumps(words)};"
                          " t.dispatchEvent(new Event('input')); })()")

    def button(self, act: str) -> str:
        return f"document.querySelector('{REL}[data-keep=\"{self.rid}\"] [data-rvact=\"{act}\"]')"

    def thread_row(self, words: str) -> str:
        return (f"[...document.querySelectorAll('{REL}[data-keep=\"{self.rid}\"] .rvthread .dentry.comment')]"
                f".find(r => r.innerText.includes({json.dumps(words)}))")

    def wait_state(self, row: str, state: str, timeout=30):
        b = S["browser"]
        self.assertTrue(b.wait(f"(() => {{ const r = {row}; return !!r && !!r.querySelector('.rstate')"
                               f" && r.querySelector('.rstate').innerText.startsWith({json.dumps(state)}); }})()",
                               timeout),
                        f"never reached '{state}': "
                        + str(b.eval(f"(() => {{ const r = {row}; return r && r.innerText; }})()")))

    def fire(self, expr: str, what: str):
        """An armed action: the first click arms, the second fires."""
        b = S["browser"]
        b.eval(f"{expr}.click()")
        self.assertTrue(b.wait(f"{expr}.classList.contains('armed')", 5), f"{what} did not arm")
        b.eval(f"{expr}.click()")

    # — the cases —

    def test_from_activity_to_the_exact_review_in_both_themes(self):
        b = S["browser"]
        for dark in (False, True):
            self.open_card(dark=dark)
            mode = "dark" if dark else "light"
            # before: what Reviews drew for this card until task 84
            self.reviews()
            b.eval("document.querySelector('#dsec-reviews').innerHTML ="
                   " '<p class=\"acount\"><b>0</b> open · <b>0</b> fix reported · <b>0</b> resolved</p>'"
                   " + '<p class=\"dnote\">No open findings on record.</p>'"
                   " + '<h3>Review rounds</h3><p class=\"dnote\">No PR review has been filed on this card.</p>'")
            shot(f"relevance-reviews-before-{mode}-1280x900")
            # after: Activity links to the exact review
            b.click("#dtab-activity")
            link = f"#dsec-activity [data-goreview=\"{self.rid}\"]"
            self.assertTrue(b.wait(f"!!document.querySelector('{link}')", 5),
                            "Activity does not link to the review")
            self.assertNotIn("Recommendation: update the task",
                             b.eval("document.querySelector('#dsec-activity').innerText"),
                             "Activity draws a second copy of the review")
            # by keyboard: focus the link and press Enter
            b.eval(f"document.querySelector('{link}').focus()")
            b.key("Enter")
            self.assertTrue(b.wait(f"!document.querySelector('#dsec-reviews').hidden"
                                   f" && document.querySelector('{REL}[data-keep=\"{self.rid}\"]').open", 5),
                            "the link did not open the review in Reviews")
            self.assertTrue(b.wait(f"document.activeElement === document.querySelector("
                                   f"'{REL}[data-keep=\"{self.rid}\"] > summary')", 5),
                            "focus did not land on the review")
            groups = b.eval("[...document.querySelectorAll('#dsec-reviews .rvgroup > h3')]"
                            ".map(h => h.innerText)")
            self.assertEqual([g.split()[0] for g in groups], ["Relevance", "PR"])
            text = b.eval("document.querySelector('#dsec-reviews').innerText")
            self.assertIn("Hazel", text)
            self.assertIn("Partly done", text)
            self.assertIn("not recorded", text, "an unrecorded revision says so")
            self.assertIn("No PR review has been filed on this card.", text)
            # untrusted report content stays inert
            self.assertEqual(b.eval(f"document.querySelectorAll('{REL} .rmd script, {REL} .rmd img').length"), 0)
            hrefs = b.eval(f"[...document.querySelectorAll('{REL} .rmd a')].map(a => a.getAttribute('href'))")
            self.assertFalse([h for h in hrefs if h and h.lower().startswith("javascript")],
                             "a script link became a link")
            self.no_sideways_scroll(f"1280x900 {mode}")
            shot(f"relevance-reviews-after-{mode}-1280x900")
        # the Overview links to it too
        b.click("#dtab-overview")
        self.assertTrue(b.wait(f"!!document.querySelector('#dsec-overview [data-goreview=\"{self.rid}\"]')", 5),
                        "the Overview does not link to the relevance review")
        b.click("#closeDrawer")

    def test_comment_draft_and_live_update(self):
        b = S["browser"]
        self.open_card()
        self.reviews()
        brief = self.brief()
        self.type_in("Agree: drop the headline bullet")
        b.eval(f"{self.button('comment')}.click()")
        row = self.thread_row("Agree: drop the headline bullet")
        self.assertTrue(b.wait(f"!!{row}", 10), "the comment never appeared in the review's thread")
        self.assertIn(f'"review": "{self.rid}"', self.text(), "the link is in the card")
        self.assertEqual(self.brief(), brief, "a comment changed the brief")
        self.assertEqual(self.agents(), [], "a comment launched an agent")
        # it survives a reload
        self.open_card()
        self.reviews()
        self.assertTrue(b.wait(f"!!{row}", 10), "the comment did not survive a reload")

        # a draft in progress survives a new review arriving live
        self.type_in("half a thought")
        b.eval(f"{DRAWER}.scrollTop = document.querySelector('{REL}[data-keep=\"{self.rid}\"] .rvcompose').offsetTop - 60")
        b.eval(f"{DRAWER}.dispatchEvent(new Event('scroll'))")
        kept = b.eval(f"document.getElementById('rvbox-{self.rid}').getBoundingClientRect().top")
        with self.card.open("a", encoding="utf-8") as fh:
            fh.write(LATER)
        self.assertTrue(b.wait(f"document.querySelectorAll('{REL}').length === 2", 20),
                        "the new review never arrived")
        self.assertEqual(b.eval(f"document.getElementById('rvbox-{self.rid}').value"), "half a thought",
                         "the live update lost the draft")
        self.assertEqual(b.eval("document.activeElement && document.activeElement.id"), f"rvbox-{self.rid}",
                         "the live update took focus from the draft")
        self.assertLessEqual(abs(b.eval(f"document.getElementById('rvbox-{self.rid}').getBoundingClientRect().top") - kept),
                             4, "the live update moved what the reader was writing")
        first = b.eval(f"document.querySelector('{REL} .rvwho').innerText")
        self.assertIn("Wren", first, "the new review is not listed first in its group")
        shot("relevance-reviews-live-update-1280x900")
        b.click("#closeDrawer")

    def test_update_card_from_this_review_fail_retry_apply_and_discard(self):
        b = S["browser"]
        backend = S["backend"]
        self.open_card()
        self.reviews()
        stage_dir = self.card.parent
        brief = self.brief()

        # no reply written: the stub refuses, so the run fails honestly
        backend.refine_reply.unlink(missing_ok=True)
        self.type_in("Take its recommendation")
        self.fire(self.button("refine"), "Update card from this review")
        row = self.thread_row("Take its recommendation")
        self.wait_state(row, "failed")
        self.assertIn("Nothing was applied.", b.eval(f"{row}.innerText"))
        self.assertEqual(self.brief(), brief, "a failed run changed the brief")
        shot("relevance-reviews-refine-failed-1280x900")

        # retry, now with a proposal
        backend.refine_reply.write_text(proposal(REWORDED), encoding="utf-8")
        self.fire(f"{row}.querySelector('button[data-act=\"retry\"]')", "Retry")
        self.wait_state(row, "proposal")
        said = b.eval(f"{row}.innerText")
        self.assertIn("Asked from relevance review", said)
        self.assertIn("not another review and not an implementation run", said)
        diff = b.eval(f"{row}.querySelector('.rdiff').innerText")
        self.assertIn("Hold the layout at 390px", diff)
        self.assertEqual(self.brief(), brief, "a proposal changed the brief")
        shot("relevance-reviews-refine-proposal-1280x900")

        self.fire(f"{row}.querySelector('button[data-act=\"apply\"]')", "Apply")
        self.assertTrue(b.wait("[...document.querySelectorAll('#disc-live .dentry.decision')]"
                               ".some(d => d.innerText.includes('applied'))", 15),
                        "the applied decision never appeared")
        self.assertIn("Hold the layout at 390px", self.brief())
        self.assertIn("## Relevance review — 2026-11-10 14:22 (Hazel)", self.text(),
                      "the review stayed in the history")
        decision = [e for e in self.discussion()["entries"] if e["kind"] == "decision"][-1]
        self.assertEqual(decision["review"], self.rid, "the decision names its review")
        self.assertEqual(self.card.parent, stage_dir, "no stage move")
        self.assertTrue(all(a.get("mode") == "refine" for a in self.agents()),
                        "something other than a refinement was launched")

        # a second proposal from the same review, discarded
        applied = self.brief()
        backend.refine_reply.write_text(proposal(REWORDED.replace("390px", "320px")),
                                        encoding="utf-8")
        self.type_in("One more pass")
        self.fire(self.button("refine"), "Update card from this review")
        row = self.thread_row("One more pass")
        self.wait_state(row, "proposal")
        b.eval(f"{row}.querySelector('button[data-act=\"discard\"]').click()")
        self.assertTrue(b.wait("[...document.querySelectorAll('#disc-live .dentry.decision')]"
                               ".some(d => d.innerText.includes('discarded'))", 15),
                        "the discarded decision never appeared")
        self.assertEqual(self.brief(), applied, "Discard changed the brief")
        S["metrics"]["relevanceReviews"] = {"entries": len(self.discussion()["entries"])}
        b.click("#closeDrawer")

    def test_390px_and_keyboard(self):
        b = S["browser"]
        for dark in (False, True):
            self.open_card(width=390, height=844, dark=dark)
            self.reviews()
            self.no_sideways_scroll("390x844")
            # every control of the review is inside the drawer
            right = b.eval(f"(() => {{ const d = {DRAWER}.getBoundingClientRect();"
                           f" return Math.max(...[...document.querySelectorAll('{REL} button, {REL} textarea')]"
                           " .filter(e => e.offsetParent).map(e => e.getBoundingClientRect().right - d.right)); })()")
            self.assertLessEqual(right, 1, "a review control spills out at 390px")
            # Tab from the textarea reaches Comment, then Update card from this review
            b.eval(f"document.getElementById('rvbox-{self.rid}').focus()")
            b.key("Tab")
            self.assertEqual(b.eval("document.activeElement.dataset.rvact"), "comment")
            b.key("Tab")
            self.assertEqual(b.eval("document.activeElement.dataset.rvact"), "refine")
            names = b.eval(f"[...document.querySelectorAll('{REL} .rvcompose button')].map(e => e.innerText.trim())")
            self.assertIn("Comment", names)
            self.assertTrue(any("Update card from this review" in n for n in names))
            label = b.eval(f"document.querySelector('label[for=\"rvbox-{self.rid}\"]').innerText")
            self.assertTrue(label.strip(), "the comment box has no label")
            b.eval(f"document.getElementById('rvbox-{self.rid}').scrollIntoView()")
            shot(f"relevance-reviews-{'dark' if dark else 'light'}-390x844")
        b.viewport(1280, 900)
        b.click("#closeDrawer")


if __name__ == "__main__":
    unittest.main()
