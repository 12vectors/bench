# 57 — The Phases view: a swimlane each

**Status:** In Progress
**Assignee:** istos
**Priority:** High — card 56 takes the members off the Board; this is
where they go
**Type:** Feature
**Depends on:** 56 — the members have to have left before they need a room

A fourth view beside Board, Sessions and Focus. One swimlane per phase,
each running the same five stages left to right, holding every card that
phase owns. It is the room where horizontal work gets horizontal space,
which the five columns could never spare.

## Context

- The exploration and both mockups, including the table of which control
  belongs to which view:
  https://claude.ai/code/artifact/727a3b64-1354-4fb0-a73d-b700bdfc2b19
- Views already exist as a switcher: `setView()` (`board.html:1005`),
  `VIEW_TITLES` (`:860`), `S.view`, and the `#views button` elements. A
  fourth is an addition to a pattern, not a new one.
- Everything to draw a lane is already in the state payload:
  `phases.public_state()` gives the runner's last pass per phase card —
  progress, the member in flight, whether it halted and why — and
  `weave_phases` has already told each member its phase and position.
- The phase card keeps its own record in a `## Phase log` section, one
  line per decision the runner made. It is the thing you read when
  something went sideways, and it has nowhere to be shown today.
- `stop_phase()` (`phases.py:662`) holds the phase *and* the agent it has
  in flight; `start_phase()` runs it again and clears a halt.

**Affected areas:** `manager/core/board.html` — a new view and its
rendering. The API is already sufficient.

## What to build

- **A Phases view** in the switcher, carrying a count of running phases
  and, when one has halted, a mark that says so from the Board view
  without switching to find out.
- **One lane per phase**, ordered so a halted phase is not below the fold.
  A lane has a head — the phase, its progress, the member in flight — and
  five stage columns holding its cards.
- **The cards are the cards.** Full fidelity: the live agent line, the CI
  and PR chips, the ordinary hover actions. Nothing is shrunk to a token,
  which is the advantage a whole view buys.
- **The last column is the phase's own**, not `done/`: a member merged
  into the phase branch is finished as far as the phase is concerned and
  is not in `main` yet. Name it for what it is.
- **The phase log under the lane**, the runner's decisions in order. It is
  the only thing that can distinguish "not reached yet" from "started and
  ended badly".
- **A halted lane says so at the top of itself** — the reason, the card it
  stopped on — and offers **▸ run again**. That action lives here and not
  on the Board, because clearing a halt should mean having read what
  caused it.
- **Per-phase controls in the lane head**: hold, the phase branch, the
  phase card. Per-member controls are the card's own and need no
  special-casing.
- **Nothing here that ends the phase.** Merging is a board move on the
  phase card, and there should be exactly one place where work leaves the
  board.

**Out of scope** — tempting neighbours left alone:

- Starting a phase, which stays on the phase card in the Board view: the
  commitment is the card reaching `in-progress/`.
- **Merge & clean up**, for the same reason.
- Editing a phase's list — reordering, adding, removing — which is the
  file's business and card 51's action.
- A per-phase Focus view. Focus is a heads-up for one session; this is a
  different thing wearing similar words.

## Acceptance

- [ ] With at least one phase in `in-progress/`, the switcher offers
      Phases and the view draws one lane per phase.
- [ ] A lane shows every card the phase lists, in the stage each is
      actually in, with its position in the run.
- [ ] A member's card in the lane offers the same actions it would offer
      on the Board.
- [ ] A halted phase names the reason and the card, offers **▸ run
      again**, and running it again clears the halt and continues.
- [ ] **‖ hold** in the lane head stops the phase and the agent it has in
      flight, and unwinds nothing.
- [ ] The phase log is readable under the lane, in the order the runner
      wrote it.
- [ ] Given a phase halts while you are on the Board view, you learn it
      there — the switcher marks it, and the existing toast and ticker
      still fire.
- [ ] With no phases at all, the view says so plainly rather than
      rendering an empty grid.
- [ ] Edge case: a phase whose members are all merged, waiting on its own
      PR, still draws a lane rather than disappearing before you have
      merged it.

## Notes

The reason this is a view and not a panel: five columns cannot hold two
boards at once, and every attempt to make them — a rail, a band, a thread
— either fights the geometry or hides the order. Given a room of its own,
the swimlane is the obvious drawing, and it was the obvious drawing all
along.

The thing to get right is not the lane, it is the **crossing**: a person
on the Board must learn that a phase halted without being on this view,
and a person here must be able to get back to the card that owns it. Two
signposts, both cheap, and the feature is untrustworthy without either.
