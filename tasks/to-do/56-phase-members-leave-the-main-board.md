# 56 — Phase members leave the main board

**Status:** To Do
**Assignee:** istos
**Priority:** High — the main board is the thing phases currently spoil,
and every other phase-UI card is downstream of this one
**Type:** Feature

A phase's members stop appearing on the Board view. The phase card stays —
one card, in whatever stage the phase is in — and its members are drawn
somewhere else (card 57). The Board goes back to meaning one thing: the
work you are personally holding, in five columns that fit.

## Context

- Membership is already derived, not stored: `taskfiles.weave_phases()`
  (`manager/core/taskfiles.py:163`) resolves every phase card's `## Cards`
  list against the board and gives each member a
  `phase: {file, number, title, index, total}`. Everything this card
  needs is on the task by the time `collect()` returns.
- `httpd.state_payload()` already ships `phases: phases.public_state()`
  (`:42`) beside the board, and `board.html` reads it in three places, so
  the client can tell a phase card from an ordinary one without asking
  again.
- `renderBoard()` (`board.html:945`) builds each column from
  `stage.tasks` and prints `stage.tasks.length` in the header, with a note
  beside it (`STAGE_NOTE[stage.slug]`, or "N agents" when agents are
  working there).
- The design exploration that led here, including the two mockups and the
  controls split:
  https://claude.ai/code/artifact/727a3b64-1354-4fb0-a73d-b700bdfc2b19

**Affected areas:** `manager/core/board.html` — the Board view's
rendering and its column headers. No server change: the state payload
already says everything needed.

## What to build

- **A member is not drawn on the Board view.** It keeps its stage, its
  file, its agent and its actions — it is simply not in this view. The
  card is not deleted, moved, or marked; the Board just stops listing it.
- **Counts describe what is visible.** A column's number is the number of
  cards you can see in it. This is the rule that keeps the header honest:
  the count is true by construction rather than true-with-a-footnote.
- **A note where the work went**, beside the count and in the register the
  existing note uses — `+2 in phases`. It is a signpost to the other
  view, not a correction to the number, and it only appears where members
  are actually hidden.
- **Membership is the only thing that hides a card.** So when a phase
  reaches `done/`, is archived, or has its `## Cards` list edited, its
  former members reappear on the Board in whatever stage they are
  genuinely in — with no sweep, no migration and no second rule. Removing
  membership *is* the un-hiding.
- **The phase card carries the summary it now owes.** It is the only
  thing standing for that work on this view, so it shows progress
  (`1 of 2 merged`), the member in flight, and a way through to the
  Phases view.
- **Only the Board view hides.** Sessions and Focus are about runs and
  sessions, not stages, and a phase member's agent is an agent like any
  other.

**Out of scope** — tempting neighbours left alone:

- Where the members are drawn instead — card 57. Until it lands they are
  reachable through the phase card and through Sessions, which is thin
  but not a dead end.
- Refusing to move a phase card — card 58.
- What happens to members' stages when the phase merges — card 59.
- Hiding anything that is not a phase member.

## Acceptance

- [ ] Given a phase with two members, when the Board view renders, then
      the members are absent and the phase card is present.
- [ ] The column that held a member counts one fewer, and its header says
      `+1 in phases`.
- [ ] A column with no hidden members shows no such note.
- [ ] Given the phase card reaches `done/`, when the board redraws, then
      its former members appear in the columns matching their actual
      stages.
- [ ] The same is true when the phase card is archived, and when a number
      is removed from its `## Cards` list.
- [ ] The header's live-agents chip still counts an agent working on a
      hidden member — hiding cards must not hide activity.
- [ ] Edge case: a card listed by a phase card that does not resolve
      (`phaseDrift`) is **not** hidden — an authoring mistake must not
      make a card vanish.
- [ ] Edge case: with no phases on the board at all, the Board view is
      byte-for-byte what it is today.

## Notes

The risk this card takes is stated plainly and should stay stated: **a
board that does not show everything is a board that can surprise you**,
and "you can see everything the agents are doing" is most of what bench
is for. Three things pay that back — the count note, the phase card's own
progress line, and the halt reaching the header whether or not you are on
the Phases view (57). If those three are not in place, this card makes
the product worse rather than better.

Worth watching for once it lands: a phase with a member in `review/`
means `review/` says `0` and yet something genuinely is waiting on you.
The note covers it, but "your move" as a column note is now a claim about
visible cards only.
