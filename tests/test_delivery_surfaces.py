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

# the module, not the class: a class imported by name would be collected
# here a second time and run as itself
from tests import test_delivery_status as matrix

import delivery  # noqa: E402
import phases  # noqa: E402
import taskfiles  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HTML = (REPO / "manager" / "core" / "board.html").read_text(encoding="utf-8")
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


FULL_HARNESS = """
const S = { state: { delivery: {} }, dfull: {} };
let now = 1000;
Date.now = () => now;
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, at: now + ms }); return timers.length; };
const asked = [];
const answers = [];
globalThis.fetch = (url) => {
  asked.push(url);
  return new Promise((resolve, reject) => answers.push({ resolve, reject }));
};
const reply = (i, status, body) => answers[i].resolve({
  ok: status === 200, json: () => Promise.resolve(body) });
const tick = () => new Promise(r => setImmediate(r));
const task = { file: 'c.md', stage: 'review', mtime: 1 };
const compact = (detail) => ({ status: { word: 'ready-to-merge' }, counts: { required: 1, met: 1 },
  candidate: { state: 'present', commit: 'abc' }, classes: ['met'], detail });
const rows = (name) => ({ detail: name, requirements: [{ kind: 'check', name }] });
let repaints = 0;
const done = () => { repaints++; };
"""

FULL_FUNCS = "\n".join([
    const("DFULL_RETRY_MS"), "let dfullSeq = 0;",
    "function deliveryOf(task) { return (S.state.delivery || {})[task.file] || null; }",
    lift("deliveryFullKey"), lift("storeDeliveryFull"), lift("loadDeliveryFull"),
])


@unittest.skipUnless(NODE, "node not available — the page's functions are unchecked")
class TheFullReadingFollowsTheServer(unittest.TestCase):
    """The supervisor's finding at 4618dd5: the Overview's requirement rows
    were cached by a key holding only the summary, so a rule moved from
    one check to another with the same totals kept the old rows for good.
    These drive the page's own `loadDeliveryFull` with a fetch the test
    answers, in the order the test chooses."""

    def run_js(self, body: str) -> dict:
        script = FULL_HARNESS + FULL_FUNCS + "\n(async () => {\n" + body + \
            "\n})().catch(e => { console.error(e.stack); process.exit(1); });"
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_rule_replaced_with_the_same_summary_is_read_again(self):
        found = self.run_js("""
          S.state.delivery['c.md'] = compact('d-old');
          loadDeliveryFull(task, done);
          reply(0, 200, { delivery: rows('old-unit') }); await tick(); await tick();
          const first = loadDeliveryFull(task, done);
          // the rule moves to another check; word, counts, classes unchanged
          S.state.delivery['c.md'] = compact('d-new');
          const during = loadDeliveryFull(task, done);
          reply(1, 200, { delivery: rows('new-unit') }); await tick(); await tick();
          const after = loadDeliveryFull(task, done);
          console.log(JSON.stringify({ asked: asked.length, first: first.detail,
            during: during.detail, after: after.detail, repaints }));
        """)
        self.assertEqual(found["first"], "old-unit")
        self.assertEqual(found["during"], "old-unit",
                         "the last rows stay drawn while the new ones are asked for")
        self.assertEqual(found["after"], "new-unit")
        self.assertEqual(found["asked"], 2)

    def test_an_unchanged_reading_is_not_asked_again(self):
        found = self.run_js("""
          S.state.delivery['c.md'] = compact('d');
          loadDeliveryFull(task, done);
          reply(0, 200, { delivery: rows('unit') }); await tick(); await tick();
          for (let i = 0; i < 5; i++) loadDeliveryFull(task, done);
          console.log(JSON.stringify({ asked: asked.length }));
        """)
        self.assertEqual(found["asked"], 1)

    def test_an_older_reply_landing_last_never_overwrites_a_newer_one(self):
        found = self.run_js("""
          S.state.delivery['c.md'] = compact('d-1');
          loadDeliveryFull(task, done);
          S.state.delivery['c.md'] = compact('d-2');
          loadDeliveryFull(task, done);
          reply(1, 200, { delivery: rows('second') }); await tick(); await tick();
          reply(0, 200, { delivery: rows('first') }); await tick(); await tick();
          const held = loadDeliveryFull(task, done);
          console.log(JSON.stringify({ asked: asked.length, held: held.detail, repaints }));
        """)
        self.assertEqual(found["asked"], 2)
        self.assertEqual(found["held"], "second")
        self.assertEqual(found["repaints"], 1, "a dropped reply repaints nothing")

    def test_a_failed_read_says_so_keeps_the_last_rows_and_retries_after_a_wait(self):
        found = self.run_js("""
          S.state.delivery['c.md'] = compact('d-1');
          loadDeliveryFull(task, done);
          reply(0, 200, { delivery: rows('kept') }); await tick(); await tick();
          S.state.delivery['c.md'] = compact('d-2');
          loadDeliveryFull(task, done);
          reply(1, 500, { error: 'boom' }); await tick(); await tick();
          const failed = { ...S.dfull['c.md'] };
          for (let i = 0; i < 3; i++) loadDeliveryFull(task, done);
          const beforeWait = asked.length;
          now += DFULL_RETRY_MS + 1;
          const retryTimer = timers.length > 0;
          loadDeliveryFull(task, done);
          reply(2, 200, { delivery: rows('fresh') }); await tick(); await tick();
          console.log(JSON.stringify({ error: failed.error, kept: failed.data.detail,
            beforeWait, retryTimer, after: asked.length,
            now: S.dfull['c.md'].data.detail, cleared: S.dfull['c.md'].error }));
        """)
        self.assertEqual(found["error"], "boom")
        self.assertEqual(found["kept"], "kept", "the last rows are kept, marked")
        self.assertEqual(found["beforeWait"], 2, "an error is not re-asked on every repaint")
        self.assertTrue(found["retryTimer"], "a repaint is scheduled for the retry")
        self.assertEqual(found["after"], 3)
        self.assertEqual(found["now"], "fresh")
        self.assertIsNone(found["cleared"])

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
        self.assertEqual(rows["Bench review"][0], "no Bench verdict yet")

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
