# 11 — A failed agent run must leave a visible trace on the card

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/22
**Assignee:** istos
**Priority:** High — three agents died today and the board showed nothing a human would notice
**Type:** Feature

When a headless agent exits non-zero, the board's entire feedback is
one ticker line ("‹name› exited on ‹file› rc=1 — see its log") that
scrolls away in seconds. The card keeps sitting in in-progress looking
exactly as it did before launch; the log's contents (today: an API 500)
never reach the UI. Three launches (06, 07, 08) died within a minute
during an API outage and the owner's experience was "they just stopped,
no feedback". Failure must be a state the card wears, not an event that
evaporates.

## Context

- `manager/core/agents.py:331-334` — the failure arm of `_reap_agent`:
  compose one summary line, record one ticker event, done. Compare the
  richer arms above it: decline moves the card back with a reason;
  clean-exit-no-commits (card 05's guard) holds the card with a loud
  message. Failure is the least-handled outcome despite being the most
  urgent one.
- The design system already has the vocabulary: `--alarm` terracotta
  means "blocked, failed or HIGH" (CLAUDE.md, Live view), and cards
  already wear state borders/pills for PR verdicts (approved / changes
  asked). There is simply no "last run failed" state.
- The log exists on disk (`local/state/agent/logs/…`) and its tail is
  usually the whole story ("API Error: 500 …"), but nothing in the UI
  displays it — "see its log" points at a file path the ticker doesn't
  even name.
- Affected areas: `agents.py` (record the outcome), `state.py`/API
  payload (expose it), `board.html` (wear it).

## What to build

- Record the outcome on the agent's session record: exit code, ended-at,
  and the cleaned last few lines of the log as the failure excerpt.
- The card wears it: a card in in-progress whose most recent run failed
  gets the `--alarm` treatment — border plus a `run failed` pill in the
  status slot — until the next launch replaces the state or the card
  moves stage. Hovering (or the card sheet) shows the excerpt, so "API
  Error: 500" is one hover away instead of buried in
  `local/state/…/logs`.
- The toast on failure, not just a ticker line: failures are rare and
  actionable, exactly what toasts are for.
- Same treatment for all headless kinds — work, review, act-pr,
  relevance — including launches that die before the agent speaks
  (today's MultiEdit flag error produced a 91-byte log; the excerpt
  handles it fine).

- Clear the way for relaunch: a failed run with zero commits leaves its
  worktree behind, and start-work refuses while it exists — so today a
  failed card needs hand `git worktree remove` before ▸ start work
  functions again. Reuse `_discard_untouched_worktree` (already called
  on declines) in the failure arm: nothing of value is lost (zero
  commits), and relaunch becomes one click. A failed run *with* commits
  keeps its worktree, same as declines do.

Out of scope: retries/auto-relaunch (a failed run is a human decision
point, and API-outage storms would make auto-retry a thundering herd);
distinguishing failure *causes* beyond showing the excerpt.

## Acceptance

- [ ] Kill a launch artificially (stub binary exiting 1 with a line of
      output): the card immediately shows the alarm border + `run
      failed` pill, a toast fires, and the excerpt is readable from the
      card without opening files on disk.
- [ ] The state clears on relaunch and on stage move, and does not leak
      onto other cards or survive into review/.
- [ ] A failed review/relevance run (no worktree, card not in
      in-progress) surfaces the same way on its card.
- [ ] Ticker line still appears (the permanent record in the event log
      is unchanged — this card adds surfaces, it does not move them).

## Open questions

- None.

## Notes

Born from the 2026-07-29 API outage: agents on 06, 07 and 08 died with
500/529 within a minute, review-pr on 02 died on the MultiEdit fossil
(card 10), and every one of them looked identical to "nothing
happening". The empty-branch guard from card 05 fixed the silent
*success* path; this is its sibling for the failure path.


---

## Work report — 2026-07-30 13:10 (Milo)

nded` on every run and, when a run exits non-zero and was not held, calls the new `_record_failure`. That puts a `failure` dict on the run's record (exit code, ended-at, the cleaned tail of the log as the excerpt, the stage the card was in, the log path) and broadcasts a toast. Because it hangs off `_finish`, all four headless kinds get it — work, act-pr, PR review, relevance check. `_agent_public` exposes `failure` and `ended`, so `/api/state` carries them. The failure arm of `_reap_agent` now also calls `_discard_untouched_worktree`: a dead run with zero commits leaves no worktree behind, so **▸ start work** functions without a hand `git worktree remove`; a dead run *with* commits keeps its worktree. All four ticker lines now name what the log ended on instead of saying "see its log".
- `manager/core/watch.py` — the move branch of `narrate` calls `agents.forget_failure(file)`, so a card that changes stage arrives without the alarm. (`agents` sits left of `watch` in the module map, so the import direction holds.)
- `manager/core/board.html` — `lastRunOn` / `failedRun` / `whyFailed`; a `.card.run-failed` alarm border, a `run failed` pill carrying the excerpt as its tooltip, and an alarm well on the card showing `rc=N · <the line it died on>`. The card sheet shows the whole excerpt in a bounded `<pre>` plus the log path. The failure outranks a PR verdict, and is only worn on the stage the run died in.
- `AGENTS.md` — a new "A run that died" subsection documents the state, its scoping and the worktree cleanup; the stale `.agent/logs/` path in the sentence above it is corrected to `local/state/agent/logs/`.
- `tests/test_failed_run_visible.py` — real launches through a real stub adapter (scripts that print a line and exit 1, that commit then exit 1, and that exit 1 silently), covering the record, the toast, the surviving ticker line, the public payload, the worktree cleanup and the successful relaunch after it, a dead relevance check, excerpt/headline edge cases, `forget_failure` scoping, and source-level invariants for board.html.

**What a reviewer should look at first**

- `manager/core/agents.py:452` (`_finish`) — recording the failure for every kind in one place is the design decision the rest follows from.
- `manager/core/board.html:720` (`failedRun`) — the two-part scoping (most recent run, and only in the stage it failed in) is what keeps the alarm off other cards and out of review/.

**One thing to know:** I verified the frontend logic by extracting `lastRunOn` / `failedRun` / `whyFailed` and running them under `node` against fabricated state (failure shows on its own card in its stage, disappears when the card is in review/, does not appear on another card, is superseded by a newer run, and the headline is the log's last line), plus the source-level invariants in the test file. I did not open the board in a browser to look at the rendered card, so the visual — border, pill and well together — is the one thing worth eyeballing before merge.


---

## PR review — 2026-07-30 14:12 (Cleo)

PR REVIEW: APPROVE

The work is complete, committed on `task/11-failed-agent-runs-must-be-visible`, mergeable (CLEAN), and CI-green on Python 3.11 and 3.13 — I could not run the suite locally (this sandbox blocks directory creation and worktrees), but the new `tests/test_failed_run_visible.py` passes on CI. A failed agent run now leaves a durable trace the card wears, and every acceptance criterion is met.

**What I checked and found good**
- **Recording (`agents.py`).** `_finish` stamps `ended` and, on a non-zero non-stopped exit, calls `_record_failure`, which puts `{rc, ended, excerpt, stage, log, mode}` on the run record and fires one error toast. Because it hangs off `_finish`, all four headless kinds inherit it — work, act-pr, PR review, relevance. `_agent_public` exposes `failure`/`ended`, so `/api/state` carries it.
- **The excerpt** (`_failure_excerpt` → `_clean_log`) is the cleaned log tail, handles a launch that died before speaking ("no output …"), and survives the 91-byte MultiEdit-fossil case.
- **Card surfaces (`board.html`).** Alarm border, `run failed` pill with the excerpt as tooltip, an alarm well showing `rc=N · <last line>`, and the whole excerpt + log path in the card sheet.
- **Scoping is double-guarded** — the one thing that keeps the alarm off other cards and out of review/: `failedRun` gates on the *newest* run (`lastRunOn` is a max-by-`started`) AND `failure.stage === task.stage`; separately `watch.narrate` calls `agents.forget_failure` on any stage move. Relaunch clears it because the newer running record wins.
- **Worktree cleanup is safely scoped.** `_discard_untouched_worktree` is added only to `_reap_agent` and no-ops unless `base` is set with zero commits, so act-pr's persistent worktree/branch (base=None) is never deleted. A failed work run with commits keeps its worktree.
- **Layering/DoD.** `watch`→`agents` respects the module-map direction with no import cycle; the ticker (permanent record) is preserved on every reaper; AGENTS.md documents the state and corrects the stale `.agent/logs/` path to `local/state/agent/logs/` (confirmed: `STATE=local/state`, `AGENT_DIR=state/agent`).

**To know (no action required to merge)**
- The rendered card — border + pill + well together — was verified via source-level invariants and `node`, not eyeballed in a browser. Acceptance #1 is visual; a 10-second glance at a real failed card is worth it but not a blocker.

**One process note**
- The formal GitHub approval could not be posted because the PR's author identity is the same `istos` this board acts as; the verdict is on the PR as [a comment](https://github.com/12vectors/bench/pull/22#issuecomment-5130649491). A human with a distinct account can click Approve if a green check is wanted before merge.
