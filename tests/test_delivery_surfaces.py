"""One delivery status, drawn the same on every surface (task 99, B1/B4).

Every row of the #94 copy matrix (`TheCopyMatrix`, F1–F32) is computed by
the real server on disposable fixtures, then handed — exactly as
`/api/state` carries it — to the page's own functions, lifted out of
`board.html` and run in node: the card face's pill, the Overview's status
block and the merge dialog's status block. The assertions compare the
server's semantics (the word, the primary action and whether it is
enabled), never CSS.

- **B1** — the three surfaces show the same word and the same primary
  action, and the face wears a word only when the design's face rule
  (§3.6) says it changes what to do next.
- **B4** — before review/, a requirement that lies ahead is never an
  alarm: the ⚖ chip stays off the face and out of the alarm register,
  while a genuine current failure, an override and a failed run stay on.

The browser half — 390 px and 1280 px, keyboard, the real page reading
the real backend — is `verify/browser_cases/case_delivery_status.py`.

    python3 -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tests import pagesource

# the module, not the class: a class imported by name would be collected
# here a second time and run as itself
from tests import test_delivery_status as matrix

import delivery  # noqa: E402
import phases  # noqa: E402
import taskfiles  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HTML = pagesource.page()
NODE = shutil.which("node")


def lift(name: str) -> str:
    """One top-level function's source, by brace matching."""
    start = HTML.index(f"function {name}(")
    depth, i = 0, HTML.index("{", start)
    while True:
        ch = HTML[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return HTML[start:i + 1]
        i += 1


def const(name: str) -> str:
    """One top-level `const NAME = …;` statement."""
    m = re.search(rf"^const {name} = .*?;\n", HTML, re.S | re.M)
    if not m:
        raise AssertionError(f"board.html lost const {name}")
    return m.group(0)


PRELUDE = "\n".join([
    "const S = { state: {}, accOpen: {}, dfull: {} };",
    "const fmtShort = (t) => String(t); const fmtClock = (t) => String(t);",
    const("esc"),
    lift("mix"), lift("actLabel"),
    const("FACE_WORDS"), const("EVIDENCE_ON_FACE"), const("GATE_STATES"),
    const("ACT_CONFIRM"), const("REQ_GLYPH"), const("REQ_ROW"),
    lift("deliveryOf"), lift("toneColours"), lift("deliveryPill"), lift("shortSha"),
    lift("requirementsHeadline"), lift("requirementRows"), lift("gateState"),
    lift("gateChip"), lift("deliveryButtonHtml"), lift("deliveryStatusHtml"),
])

RENDER = """
const word = (html) => (html.match(/data-word="([^"]+)"/) || [])[1] || null;
const primary = (html, slot) => {
  const m = html.match(new RegExp('data-dslot="' + slot + '"([^>]*)>(.*?)</(button|a)>', 's'));
  return m ? { disabled: /\\bdisabled\\b/.test(m[1]), text: m[2] } : null;
};
const out = {};
for (const [file, d] of Object.entries(S.state.delivery)) {
  const task = { file, stage: STAGES[file] };
  const face = FACE_WORDS.has(d.status.word) ? deliveryPill(d) : null;
  const ov = deliveryStatusHtml(task, d, 'ov');
  const dlg = deliveryStatusHtml(task, d, 'dlg');
  const chip = gateChip(task);
  out[file] = {
    face: face && face.word, faceTone: face && face.tone,
    overview: word(ov), dialog: word(dlg),
    ovPrimary: primary(ov, 'ov-p'), dlgPrimary: primary(dlg, 'dlg-p'),
    alert: /role="alert"/.test(ov),
    chip: chip && { face: !!chip.face, cls: chip.cls, label: chip.label },
  };
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node not available — the page's functions are unchecked")
class TheSurfacesAgree(matrix.TheCopyMatrix):
    """Every copy-matrix fixture, re-run, and drawn by the page."""

    def expect(self, word, primary, found=None):
        found = super().expect(word, primary, found)
        self.surfaces(found)
        return found

    def surfaces(self, found: dict) -> None:
        filename = found["task"]
        task = next(t for s in taskfiles.collect()["stages"] for t in s["tasks"]
                    if t["file"] == filename)
        delivery.forget_cached()
        # as /api/state reads it: the phase runner's last pass handed in
        compact = delivery.board({"stages": [{"tasks": [task]}]},
                                 phases.public_state())[filename]
        gates = {filename: {"scope": "acceptance", "summary": "", "location": "local",
                            "decision": "blocked"}} if compact["counts"]["required"] else {}
        script = (PRELUDE +
                  f"\nS.state.delivery = {json.dumps({filename: compact})};"
                  f"\nS.state.gates = {json.dumps(gates)};"
                  f"\nconst STAGES = {json.dumps({filename: task['stage']})};" + RENDER)
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        drawn = json.loads(out.stdout)[filename]
        status = compact["status"]

        # B1: one word on every surface, and it is the server's
        self.assertEqual(drawn["overview"], status["word"])
        self.assertEqual(drawn["dialog"], status["word"])
        self.assertEqual(status["word"], found["status"]["word"],
                         "the compact reading and the card's own reading disagree")
        on_face = status["word"] in FACE
        self.assertEqual(drawn["face"], status["word"] if on_face else None)
        # …and one primary action, enabled or disabled as the server says
        p = compact["primary"]
        for side in ("ovPrimary", "dlgPrimary"):
            if p is None:
                self.assertIsNone(drawn[side], side)
                continue
            self.assertIsNotNone(drawn[side], side)
            self.assertIn(p["label"].replace("&", "&amp;"), drawn[side]["text"])
            if p.get("action") != "open-pr-url":
                self.assertEqual(drawn[side]["disabled"], not p["enabled"], side)
        # an alarm is announced as one; nothing else is
        self.assertEqual(drawn["alert"], status["tone"] == "alarm")

        # B4: before review, a requirement that lies ahead is not an alarm
        chip = drawn["chip"]
        if chip and task["stage"] != "review" and status["word"] != "ready-overridden":
            self.assertFalse(chip["face"], "a future requirement on the face")
            self.assertNotEqual(chip["cls"], "bad", "a future requirement in the alarm")
        if chip and task["stage"] == "review" and \
                compact["counts"]["met"] < compact["counts"]["required"]:
            self.assertTrue(chip["face"], "the review step's requirements left the face")


class TheFullReadingFollowsTheServer(unittest.TestCase):
    """The supervisor's finding at 4618dd5: the Overview's requirement rows
    were cached by a key holding only the summary, so a rule moved from
    one check to another with the same totals kept the old rows for good.
    The page's own `loadDeliveryFull` is driven as a real module, with a
    fetch the test answers in the order it chooses, in
    tests/node/requests_and_rendering.test.mjs (task 125): a rule replaced
    with the same summary is read again, an unchanged reading is not, a
    newer reading stands, and a failed read keeps the last rows, marked,
    and retries after a wait."""

    def test_the_requirements_section_says_when_its_rows_could_not_be_read(self):
        self.assertIn("The requirement rows could not be read", lift("requirementsHtml"))


SIGNALS = "\n".join([
    "const S = { state: {} };", lift("shortSha"), const("REVIEW_WORD"),
    lift("reviewSignals"),
])


@unittest.skipUnless(NODE, "node not available — the page's functions are unchecked")
class TheReviewSignalsSayWhatEachRecordSays(matrix.DeliveryCase):
    """The supervisor's finding at d6ace0e: F16's Overview said *Ready to
    merge* and *Review by Bench approved at the current commit*, while
    Signals said an unqualified *Review: no verdict yet* — because it read
    only the card's prose report and GitHub's folded state. These compute
    the server's reading on disposable fixtures and draw Signals' review
    rows with the page's own `reviewSignals`."""

    def rows(self, pr=None) -> dict:
        delivery.forget_cached()
        task = delivery._find(matrix.CARD, None)
        compact = delivery.board({"stages": [{"tasks": [task]}]})[matrix.CARD]
        script = (SIGNALS + f"\nconst d = {json.dumps(compact)};"
                  f"\nconsole.log(JSON.stringify(reviewSignals(d, {json.dumps(pr)})));")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return {label: (text, cls) for label, text, cls in json.loads(out.stdout)}

    def test_f16_durable_bench_approval_with_no_report(self):
        head = self.passed_at_head()
        matrix.gates.record_verdict(matrix.CARD, verdict="APPROVE", head=head)
        self.place("review", pr=matrix.PR_URL)
        self.assertEqual(self.word(), "ready-to-merge")
        rows = self.rows()
        text, cls = rows["Bench review"]
        self.assertIn(f"approved at {head[:7]}", text)
        self.assertIn("the commit that would merge", text)
        self.assertEqual(cls, "")
        self.assertNotIn("Reported review", rows, "no report is on the card")
        for label, (text, _) in rows.items():
            self.assertNotIn("no verdict yet", text, label)
        # unobserved is said as unobserved, never as "no review"
        self.assertIn("not observed", rows["GitHub review"][0])

    def test_github_observed_without_a_review_state_is_not_unobserved(self):
        head = self.passed_at_head()
        matrix.gates.record_verdict(matrix.CARD, verdict="APPROVE", head=head)
        self.place("review", pr=matrix.PR_URL)
        none = self.rows({"verdict": "red", "review": None, "ci": "fail"})
        self.assertIn("no formal review state", none["GitHub review"][0])
        self.assertEqual(none["GitHub review"][1], "", "failing CI is not a review")
        approved = self.rows({"verdict": "green", "review": "approved"})
        self.assertIn("approved on the pull request", approved["GitHub review"][0])
        self.assertIn("not bound to a commit", approved["GitHub review"][0])
        changes = self.rows({"verdict": "red", "review": "changes"})
        self.assertEqual(changes["GitHub review"][1], "bad")

    def test_a_report_about_an_older_head_is_history_not_approval(self):
        wt = self.worktree()
        older = self.commit_in(wt)
        self.place("review", pr=matrix.PR_URL)
        path = self.tasks / "review" / matrix.CARD
        path.write_text(path.read_text(encoding="utf-8") +
                        f"\n---\n\n## PR review at {older[:12]} — 2026-11-11 18:53 (Finch)"
                        "\n\nPR REVIEW: REQUEST CHANGES\n\nFix it.\n", encoding="utf-8")
        self.commit_in(wt, "addressed\n")
        rows = self.rows()
        text, cls = rows["Reported review"]
        self.assertIn(f"changes requested in the card's report at {older[:7]}", text)
        self.assertIn("an earlier commit, history", text)
        self.assertEqual(cls, "", "a request about an older commit is not today's alarm")
        # task 152: the row leads with the server's review status — the
        # card face's words — and the text's older verdict is not today's
        # task 152 review: with no Bench verdict, the row says why it is
        # outdated in the status's own sentence rather than a bare
        # "No Bench verdict yet"
        bench_row = rows["Bench review"][0]
        self.assertTrue(bench_row.startswith("Review outdated. The card's text "
                                             "reports a PR review"), bench_row)
        self.assertIn("Bench has no recorded verdict", bench_row)
        self.assertNotIn("Review approved", bench_row)

    def test_a_report_that_is_the_bench_verdicts_prose_is_not_drawn_twice(self):
        head = self.passed_at_head()
        matrix.gates.record_verdict(matrix.CARD, verdict="APPROVE", head=head)
        self.place("review", pr=matrix.PR_URL)
        path = self.tasks / "review" / matrix.CARD
        path.write_text(path.read_text(encoding="utf-8") +
                        f"\n---\n\n## PR review at {head[:12]} — 2026-11-11 18:53 (Finch)"
                        "\n\nPR REVIEW: APPROVE\n\nGood.\n", encoding="utf-8")
        self.assertNotIn("Reported review", self.rows())


FACE = set(re.findall(r"'([a-z-]+)'", re.search(
    r"const FACE_WORDS = new Set\(\[(.*?)\]\);", HTML, re.S).group(1)))


if __name__ == "__main__":
    unittest.main()
