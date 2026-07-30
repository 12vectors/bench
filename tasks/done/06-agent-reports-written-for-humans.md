# 06 — Agent closing reports written for humans, not for the parser

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/2
**Priority:** Medium — every report lands in a task file and a PR body that a person must act on
**Type:** Feature

The closing reports headless agents leave in task files are dense and
hard to act on — accurate, but compressed to the point where the one
thing a human must do next (e.g. "nothing is committed; run the tests
first") sits buried mid-paragraph. The cause is in the prompts: the only
guidance the agent gets about its report is one line asking for "a
concise summary". Concision is the wrong instruction when the reader is
a person deciding what to do with the card.

## Context

- `manager/core/prompts/work.md`, final rule: "Finish with a concise
  summary: what changed, how it was verified, and anything a reviewer
  should look at first." No audience, no structure, no ordering — so the
  model optimises for density. Compare the report on
  `../review/02-start-cleanly-when-bench-is-the-project.md`
  ("Verification status" / "For the reviewer"): everything a reviewer
  needs is present, including the critical uncommitted-work warning, but
  nothing is scannable and the headline fact is not the headline.
- The report is load-bearing in three places: the board parses marker
  lines from it (NOT READY:, PR REVIEW: — these must survive any
  format change), it becomes the PR body when the card enters review/,
  and it is appended to the task file as the permanent record.
- Same gap in `review.md`, `review-pr.md`, `act-pr.md` — each asks for a
  verdict or summary without saying who reads it.
- Prompts are overridable per project (`local/prompts/` beats
  `core/prompts/` by filename), so this is a change to shipped defaults
  — exactly where report quality should live.

## What to build

Extend the closing-report section of all four core prompt templates
with an explicit contract. Proposed shape:

- Name the audience: a teammate who did not watch the work and will
  decide what happens to this card in under a minute.
- Demand outcome-first ordering: the first line states what happened and
  what state the work is in (committed? tested? blocked?) before any
  narrative. Anything the reader must *do* (run tests, check a file,
  answer a question) goes in its own short list, not inside prose.
- Complete sentences over fragments; no unexplained internal shorthand;
  file paths given repo-relative so they are clickable in the PR.
- Keep the machine-readable marker lines exactly as they are (first/last
  line contracts with the board's parser must not move).
- Sanity-check the length instruction: "concise" becomes "short but
  self-sufficient" — selectivity about content, not compression of
  wording.

## Acceptance

- [ ] The next work agent's report on a real task opens with the state
      of the work (including whether anything is uncommitted or
      unverified) and separates "what a human must do" from narrative.
- [ ] Board marker parsing still passes its existing tests — NOT READY
      and PR REVIEW flows unbroken.
- [ ] All four core templates carry the same report contract, phrased
      once per file (no drift between them).
- [ ] A project overriding a prompt in `local/prompts/` still wins —
      resolution order untouched.

## Open questions

- None.

## Notes

Raised after reading Cleo's card-02 report side by side with the
interactive session's summaries of the same events: the difference is
not the model, it is the brief. The interactive session operates under
detailed communication guidance (audience, outcome-first, readability
over brevity); the headless prompt says only "concise". Give the
headless agents the same brief and the reports should converge.


---

## Work report — 2026-07-29 22:37 (Reed)

The task is complete and committed: all four core prompt templates now carry an identical reader-first closing-report contract, a new test suite locks it in place, and the full suite (37 tests) passes. One commit on this branch, `509f19c`; nothing is uncommitted or unverified.

What changed:

- `manager/core/prompts/work.md`, `review.md`, `review-pr.md`, `act-pr.md` each end with the same contract block: the report's audience is a teammate who did not watch the work and will decide the card's fate in under a minute; the first sentence states the state of the work (committed, tested, blocked) before any narrative; anything the reader must *do* goes in its own short list; complete sentences, no unexplained shorthand, repo-relative paths; and "short but self-sufficient beats concise". `work.md`'s old "concise summary" rule now points at this block, and `review.md`'s "concise evidence" wording was dropped.
- Every machine-parsed marker line is byte-identical to before — `NOT READY:`, `RELEVANCE REVIEW:`, `PR REVIEW:`, `ADDRESSED:` all keep their exact wording and first-line position, and the new block deliberately never repeats those strings, so `agents.py`'s `text.find(...)` extraction cannot latch onto it.
- `tests/test_prompt_report_contract.py` (new) enforces the acceptance criteria mechanically: the contract block is *identical* across all four templates (drift in any one fails), the four marker lines survive verbatim, every template still renders through `str.format` (guarding against unescaped braces in the new prose), and `local/prompts/` overrides still beat the core defaults.

How it was verified: `python3 -m unittest discover -s tests` — 37 tests, all passing, including the three pre-existing suites (adapter permissions, empty-branch guard, self-hosting).

For the reviewer, one judgment call worth a look first: the contract block is phrased once and appended verbatim to all four files rather than factored into a shared include — the prompt loader has no include mechanism, and the drift risk is covered by the identical-block test instead. The wording itself is in `manager/core/prompts/work.md:39-60`. Whether the first *real* work-agent report actually opens outcome-first (acceptance bullet 1) can only be judged on the next launch — this change is the brief, not the behaviour.
