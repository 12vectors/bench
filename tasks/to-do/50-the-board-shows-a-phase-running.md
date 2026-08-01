# 50 — The board shows a phase running, and shows it stopping

**Status:** To Do
**Assignee:** istos
**Priority:** High — a phase that runs unattended is only trustworthy if
its halt is impossible to miss
**Type:** Feature
**Depends on:** 49 — there must be something to watch

Give the phase the three pieces of interface it does not already inherit:
an action to start and stop it, a header chip while it runs, and the same
chip in `--alarm` when it halts. Everything else — the PR chip, CI, the
review actions, the merge-and-clean-up sheet — the phase card already
gets by being a card.

## Context

- The phase card moves through the stages like any other card, and each
  stage already means the right thing: `to-do/` queued, `in-progress/`
  running, `review/` all members landed and a PR open, `done/` merged.
- Card actions live in the status pill's slot on hover, at most two per
  state, and anything costing tokens or stopping work arms on the first
  click and fires on the second (`board.html:1191` onwards).
  `▸ start work` sits in that slot on an in-progress card, and `‖ hold`
  is already the word for stopping while an agent runs.
- The header carries what is happening *across* the board: the live
  agents chip, and the sync chip which appears only when sync stops
  converging and then holds until a human settles it. A running phase is
  the same kind of fact.
- The design system's colour law: `--accent` an agent alive, `--alarm`
  blocked or failed, and one looping animation ("breathe") meaning work
  is happening.
- A failed run is deliberately told three times at three altitudes — a
  state the card wears, a toast, and a line in the ticker — because it is
  the outcome a person must not miss.

**Affected areas:** `manager/core/board.html` only, plus whatever
`/api/state` must carry to describe a running phase.

## What to build

- **`▸ run phase`** on an `in-progress/` phase card, in the slot
  `▸ start work` occupies on an ordinary one, arming and firing like
  every other launch. Moving the card to `in-progress/` stays the
  commitment; this is the second half of it.
- **`‖ hold`** while it runs, meaning what it means everywhere else:
  stop, without unwinding what has already landed on the phase branch.
- **A header chip while a phase runs**, beside the agents chip, breathing
  in `--accent`: the phase, its progress and the card it is on —
  `⟶ auth-rework · 3/5 · on #33`. Present only while a phase is running,
  the way the sync chip is present only when there is something to say.
- **The halted state, in `--alarm`, holding.** `⟶ auth-rework · halted at
  #35 — not ready`, staying until the phase is resumed or stopped rather
  than scrolling away. With a toast, because a halt is rare and
  actionable, and a ticker line that survives in the log. The member card
  wears its own failure independently — that duplication is the point.
- **Narration of each advance** in the ticker: the member that finished,
  the merge into the phase branch, the member starting next. A phase that
  advances silently is a phase nobody can debug afterwards.
- **The phase card's sheet lists its members in order** with each one's
  current stage, so the card answers "where is this up to" without
  hunting across five columns.

**Out of scope** — tempting neighbours left alone:

- Filtering the board to a phase's cards, or dimming everything else.
  Useful, bigger, and better judged once the chip exists.
- A Focus view for a phase. Focus is a heads-up display for one session;
  one for a phase is a real idea and a separate build.
- Anything that changes what the runner does. This card watches.
- The `⟶` member chip, which is card 48's.

## Acceptance

- [ ] Given a phase card in `in-progress/`, hovering it offers
      **▸ run phase**, which arms on the first click and starts on the
      second.
- [ ] While a phase runs, the header carries a breathing chip naming the
      phase, its progress and the member in flight.
- [ ] Given a member that fails, declines, commits nothing or goes red,
      the chip turns `--alarm` and names the reason and the card, a toast
      fires, and the chip holds until the phase is resumed or stopped.
- [ ] **‖ hold** stops the phase and leaves the phase branch and every
      landed member exactly as they were.
- [ ] With no phase running, the header is exactly as it is today — no
      empty chip, no placeholder.
- [ ] The ticker names every advance: what finished, what merged, what
      started.
- [ ] Opening a phase card lists its members in run order with each
      one's stage.
- [ ] Edge case: two phases could in principle run on one board — either
      the chip handles more than one, or starting a second is refused
      with a reason. Not a chip that silently shows one of them.

## Notes

The reason the halted state gets this much attention is that the feature
is asking for trust: you start a phase and stop watching. Everything in
bench that runs unattended already pays that back the same way — the
failed run wears its state, sync says when it stalls — and a phase that
halts quietly at 2am would undo the whole argument for having one.

Worth checking while building: the header is getting crowded. Agents
chip, sync chip, phase chip, theme, refresh. If they collide, the phase
chip is the one that can fold into the agents chip — both answer "what
is happening without me" — rather than shrinking the ones already there.
