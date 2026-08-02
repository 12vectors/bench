# 55 — A headless run gets one turn, and nothing tells the agent that

**Status:** In Progress
**Assignee:** istos
**Priority:** High — it costs a whole run and everything in it, and the
condition that triggers it is getting more likely every week
**Type:** Bug

A work agent finished its turn with the words *"The suite is still
running; I'll wait for the monitor rather than poll further."* There is no
monitor. A headless `claude -p` run ends when the model stops producing
output, so ending a turn to wait ends the run — and this one ended with
314 lines of finished, passing work staged and never committed.

## Context

What actually happened, on card 47 inside phase 53:

- The agent staged changes across `AGENTS.md`, `manager/core/board.html`,
  `manager/core/httpd.py`, a new `tests/test_archive_chip.py` and an
  edited `tests/test_card_actions.py` — then started the test suite and
  ended its turn to wait for it.
- The process exited 0 with no commits on `task/47-…`. The log is 80
  bytes: that one sentence.
- Everything downstream behaved correctly. The board refused to move the
  card to `review/` — an empty branch reaching review is a broken launch
  hiding — and the phase halted at 47 rather than skipping it, saying so
  in its `## Phase log`.
- The work was fine. Run by hand afterwards: 37 tests in the two files
  it touched, then **875 tests, all passing**, in that worktree.

Why waiting looked reasonable to the agent: **that suite takes four
minutes.** 875 tests, 239 seconds. This morning it was 631 tests in about
105 seconds. At four minutes, backgrounding a run and waiting for it is a
sensible-looking strategy for an agent that believes it will be resumed.

And nothing tells it otherwise. `manager/core/prompts/work.md` says to
run the checks until they pass and to commit in clear, reviewable
commits. It never says the run is a single non-interactive turn, that
there is nobody to hand back to, or that uncommitted work dies with the
process. The one place finality is stated is the `NOT READY` path — "end
immediately with a reply whose FIRST line is exactly…" — which is about
declining, not about the shape of the run.

**Affected areas:** `manager/core/prompts/work.md` first, and the same
paragraph is owed to `act-pr.md` and `review.md`, which run the same way.

## What to build

- **Say what a run is, at the top of the prompt.** One short paragraph:
  this is a single non-interactive turn; there is no human and no second
  chance; when the reply ends the process exits; anything not committed
  is lost, and the board judges the run by what is on the branch.
- **Turn that into an instruction, not a warning.** Commit before
  anything long-running, and commit again after it. A commit is cheap and
  a lost run is not — an agent that commits early can always improve on
  it in the same turn.
- **Name the trap by name.** Do not background a command and end the turn
  to wait for it; do not promise to come back to something. If a check
  takes minutes, run it in the foreground and wait inside the turn.
- **The same paragraph in the other headless prompts.** `act-pr` pushes
  and `review` posts a verdict; both lose everything the same way if the
  turn ends early.
- **Check the marker contract still reads clearly** once the paragraph is
  added — `NOT READY:` and the closing report are parsed from the same
  output, and the new text must not muddy where the first line goes.

**Out of scope** — tempting neighbours left alone:

- Making the runner resume a stalled agent, or giving an agent a second
  turn. One-shot is the design; the fix is telling it so.
- Rescuing uncommitted work automatically. Tempting, and wrong: the
  board judging a run by its commits is exactly what caught this.
- Changing what the definition-of-done checks are — see Notes.

## Acceptance

- [ ] `prompts/work.md` states, before the task body, that the run is one
      non-interactive turn and that uncommitted work is lost when it ends.
- [ ] It instructs the agent to commit before long-running commands, and
      not to end a turn waiting on one.
- [ ] `act-pr.md` and `review.md` carry the same paragraph.
- [ ] `tests/test_prompt_report_contract.py` still passes — the four
      templates keep their identical closing-report block and their
      marker lines still parse.
- [ ] Edge case: the added text does not change where the first line of a
      `NOT READY:` reply must sit.

## Notes

The recovered work from that run is committed on
`task/47-an-archive-button-on-the-card` as `661dd89`, by hand, unchanged
from what the run left staged.

**The four-minute suite is the other half of this**, and it deserves its
own card rather than a fix here. `BOARD_AGENT_COMMANDS` is
`python3 -m unittest`, so every work agent runs everything — 875 tests
now, and the number only goes up. A definition-of-done slow enough to
change how an agent behaves is a problem that gets worse quietly: the
prompt fix stops the agent walking away from it, but it does not make the
wait shorter, and the next agent will still spend four minutes of its run
watching a suite that mostly tests things it did not touch.


---

## Work report — 2026-08-02 12:51 (Otto)

The work is committed and the full suite is green. Here is the closing report.

---

**The change is committed on `task/55-a-headless-run-is-one-turn-and-must-say-so` as `f676c9f`, and the full definition-of-done suite passes: 1007 tests, OK.** All four core prompt templates now open with an identical block saying a headless run is one non-interactive turn, and each one follows it with the instruction that turn shape implies for its own kind of run.

**What changed**

- `manager/core/prompts/work.md`, `act-pr.md`, `review.md` and `review-pr.md` each gained the same block, placed before the task body: the run is a single non-interactive turn, the process exits when the reply ends, work meant for afterwards is lost with it, and the board judges the run by what was actually left behind. It then names the trap directly — do not background a command and end the turn to wait for it (there is no monitor, no notification, no resume; run a slow check in the foreground), and do not promise to come back to something.
- Each template then says what its own run loses. `work.md`: commit early and often, commit before anything long-running and again after it, because a commit is cheap and a lost run is not. `act-pr.md`: the same, plus that an unpushed commit never reaches the PR. `review-pr.md`: post the verdict to GitHub inside the turn, before the reply that ends it. `review.md`: the report is all the run leaves behind, so it goes in the reply that ends the turn.
- `tests/test_prompt_one_turn.py` is new: it asserts the block is byte-identical across the four templates, that it precedes `--- TASK ---` in each, that it names the background-and-wait trap, that it carries no braces (so `str.format` still renders every template), and that each template keeps its own follow-through instruction. A `MarkersUndisturbed` class covers the acceptance edge case: every marker line still sits after the new block, `work.md` contains `FIRST line` exactly once and still after `--- END TASK ---`, and the block itself mentions neither `FIRST line` nor `NOT READY`.

**A note on scope, since it differs slightly from the acceptance wording.** The task named `act-pr.md` and `review.md`; I added the block to `review-pr.md` too. All four are headless one-shot runs, and `review-pr.md` is the one whose side effect (`gh pr review`) is lost if the turn ends early — the task's phrase "`review` posts a verdict" describes it rather than the relevance reviewer. The identical-block test now holds all four together the way the existing report-contract test does.

**Two things to know, neither requiring action**

- The first full-suite run had one failure, `tests/test_phase_runs.py::HaltNeverSkip::test_running_it_again_clears_the_halt_and_carries_on` (`'in-progress' != 'review'`). It passed when the module was run alone and passed again in a second full run. It is a timing race in the test's `settle()` helper, which waits a bounded time for a real launched agent to be reaped, and nothing in this change can reach it — the diff is four markdown files and one test that only reads them. Worth its own card if it recurs.
- `TodoWrite` was not available in this session, so the board had no live plan to display for this run. Nothing else about the run was affected.

**What a reviewer should look at first:** the block itself in `manager/core/prompts/work.md:7-24` — it is prose an agent must act on, so its wording is the whole change. Everything else is that same text repeated and a test holding the four copies together.
