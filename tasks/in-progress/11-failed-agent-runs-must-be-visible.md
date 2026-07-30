# 11 — A failed agent run must leave a visible trace on the card

**Status:** In Progress
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
