# 57 — The Phases view: a swimlane each

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/48
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


---

## Work report — 2026-08-02 08:55 (Ada)

Committed on `task/57-the-phases-view-a-swimlane-each` as `a3388ed`, with the full suite green (939 tests).

**The Phases view is built, tested and committed.** All acceptance items are implemented; the two things a reviewer should judge with their eyes rather than the tests are the lane's visual layout and the switcher mark, because this repo has no frontend test runner (the house convention, which I followed, is node-run rules over a real `taskfiles.collect()` reading plus source-level wiring invariants).

**To do**

- Open the board on a branch with a phase card and look at the lane: the head, the five columns, and the log box under them (`manager/core/board.html`, the `── phases: a swimlane each ──` CSS and JS sections).
- Decide whether you want the header's phase chip to keep opening the phase card on the Board rather than jumping to its lane — I left that card-55 behaviour alone as out of scope.

**To know**

- One deliberate change outside the new view: a halted phase card on the Board no longer offers **▸ run phase**. The task says run-again belongs in the lane "because clearing a halt should mean having read what caused it", so the card now offers **⟶ phases** (which switches view) alongside **‖ hold**. `tests/test_phase_watch.py::test_a_halt_offers_both_ways_out` was renamed and rewritten to pin the new arrangement.
- The last column is `Merged in`, not `done/`. Membership in it comes from two sources: the runner's snapshot while a phase runs, and the `## Phase log`'s `<n> merged into <branch>` lines once the phase has left `in-progress/` and the runner stops passing over it. That second source is what makes the "all merged, waiting on its own PR" edge case draw a real lane instead of an empty one — no API change was needed, as the task predicted.
- Two other tests moved because this work changed what they counted: `tests/test_card_actions.py` (a fourth `wireAction` call site — the lane head, still the one arm-then-fire machine) and `tests/test_scroll_kept.py` (`#view-phases` and `.lstages` are new scrollers, both marked and restored).
- `AGENTS.md` gained a "The Phases view: a swimlane each" section and the views list now says four, matching how cards 55 and 56 documented themselves.


---

## PR update — 2026-08-02 09:06 (Ada)

ADDRESSED: Held (stopped) phases now sort just below running phases in the Phases view instead of tying with unstarted ones; committed as `99fb248` and pushed, full suite green (940 tests).

- **Copilot review comment (`laneRank()` treats a held/stopped phase like an unstarted one)** — Confirmed valid: the phase snapshot does carry `snap.stopped` (`phases.py:317`), but `laneRank()` returned rank 2 for both a held run and a phase nobody has started, so an actionable "stopped — held by …" lane could sort below unstarted phases. Fixed in `manager/core/board.html`: a held run now gets its own rank (`snap.stopped → 2`) just below running (`1`), with unstarted and done pushed to `3`/`4`. Updated the explanatory comment to describe the new order, and added `test_a_held_run_sorts_above_a_phase_nobody_has_started` in `tests/test_phases_view.py` to pin it (running → held → unstarted).

To know:
- The PR is `MERGEABLE` — no conflict with `main`, so no merge commit was needed.
- The overall Copilot review state was `COMMENTED` (not requesting changes); this was its single inline point, now addressed. The rest of its overview was a neutral summary with no further asks.

No action items for the reviewer.
