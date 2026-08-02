# 54 — A work agent must refuse a phase card

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/52
**Assignee:** istos
**Priority:** High — it already happened, and what it produces is an agent
implementing a card whose entire content is a list of other cards
**Type:** Bug

`▸ run phase` guards itself: `/api/phase/run` refuses anything that is not
a phase card. The other direction has no gate at all. `/api/agent/start`
accepts a phase card, cuts `task/<stem>`, and launches an ordinary
headless work agent on a brief that is a table of contents — which is
exactly what happened to card 53 the first time a phase reached the
board.

## Context

The asymmetry, in two lines of the codebase:

- `phases._phase_card()` (`manager/core/phases.py:592`) checks the stage,
  checks the file exists, and then checks the card is one: *"is not a
  phase — a phase card is `**Type:** Phase` with a `## Cards` section"*.
- `agents.start_agent()` checks the stage (`_validate`) and the claim
  (`claim_for_launch`), and nothing else. A grep of `agents.py` for
  `phase` finds the branch-point helper and no guard whatsoever.

What that produced on card 53, in order:

1. The card was moved to `in-progress/` — the commitment, and on its own
   it starts nothing, for a phase or a task.
2. **▸ start work** was clicked instead of **▸ run phase**. The UI is
   right — `board.html:1212` offers one or the other, never both — so
   this came from a browser tab loaded before the phase code merged and
   still running the old JavaScript.
3. The server took it. Worktree at `.worktrees/53-…` on `task/53-…`, an
   ordinary work agent launched with the ordinary work prompt.
4. The agent did as it was told. It read a brief listing cards 47 and 52
   and a "What done looks like" section, and implemented **both** in one
   worktree — the archive chip and the scroll fix, ~130 lines across
   `board.html`, `httpd.py` and `AGENTS.md`. Nothing in the prompt or the
   launch told it the card was a coordinator.
5. The phase never started. `advance()` returns at `phases.py:538`
   unless the phase branch exists, and cutting that branch is the whole
   of starting — so the beat looked at the card every few seconds,
   concluded it had not begun, and said nothing.

This is the failure mode the project already has a doctrine for. The
file-carried gates exist *because* a UI layer can be stale or bypassed —
"Both layers, deliberately." Phases shipped with the UI layer and without
the server one.

**Affected areas:** `manager/core/agents.py` (the guard), and
`manager/core/board.html` (what an unstarted phase looks like).

## What to build

- **`start_agent()` refuses a phase card**, in the same breath and the
  same style as it refuses a launch from the wrong stage: named, with the
  action that was meant — a phase card runs with **▸ run phase**, not
  **▸ start work**. It is one condition beside two that are already
  there.
- **Refuse before anything exists.** The check belongs with `_validate`,
  ahead of the claim and well ahead of the worktree, so a refusal costs
  nothing and leaves nothing to clean up — the same shape as every other
  launch refusal.
- **The read-only launches too.** `◔ still true?` on a phase card is
  harmless and arguably useful, but `◔ review PR` and `↻ act on PR`
  should be considered explicitly rather than by omission: decide which
  of the agent kinds a phase card may host, and say so where the guard
  lives.
- **An unstarted phase says so.** A phase card in `in-progress/` that has
  not been started looks exactly like one that is running — the header
  chip is the only difference, and it is absent in both the "not yet" and
  the "no phase at all" cases. The card should carry the distinction
  itself, quietly: not started, running, or halted.

**Out of scope** — tempting neighbours left alone:

- Making a work agent understand phase cards. It should refuse them, not
  learn to coordinate.
- Anything about how a phase runs once started.
- Stale browser tabs in general — a page that has been open across a core
  update will be wrong in other ways too, and telling it so is a
  different and much larger card.

## Acceptance

- [ ] Given a phase card in `in-progress/`, when `/api/agent/start` is
      called on it — from a stale tab, by hand, by anything — then it is
      refused with a message naming **▸ run phase**, and no worktree,
      branch or agent process is created.
- [ ] Given an ordinary card, **▸ start work** behaves exactly as it does
      today.
- [ ] Given a non-phase card, `/api/phase/run` still refuses it as it
      already does.
- [ ] A phase card in `in-progress/` that has not been started is
      visibly distinct from one that is running.
- [ ] Whichever read-only agent kinds are allowed on a phase card are
      allowed deliberately, and the rest refuse with a reason.
- [ ] Edge case: a card that gains `**Type:** Phase` *while* an ordinary
      work agent is already running on it does not crash the run — the
      guard is about starting, not about a run in flight.

## Notes

The stray run also slipped past the failure machinery: it left a 0-byte
log, no commits, and no `run failed` pill, so the only trace was a
worktree with uncommitted work in it and nothing on the board pointing at
it. That is worth its own look — a run whose board did not observe its
exit currently vanishes rather than reporting — but it is a separate
defect from this one and should not be folded in here.

Worth noticing what did *not* go wrong: card 53 parsed correctly,
`start_phase` would have refused a card with drift or an empty list
before cutting anything, and the beat was right to stay silent about a
phase nobody had started. The gate that was missing is the one on the
other feature entirely, which is the usual shape of this kind of bug —
a new capability guards its own front door and leaves the neighbour's
open.


---

## Work report — 2026-08-02 13:32 (Piper)

Committed. Final state of the work:

**What changed**

- `manager/core/agents.py:151-213` — a note stating which headless kinds a phase card may host, plus `is_phase_card()` and a `phase=` argument on `_validate()`. Passing it makes that kind refuse a phase card, naming what to do instead; omitting it is the recorded decision that the kind is allowed.
- `start_agent()` (`agents.py:360`) passes it: a phase card is refused with a message naming **▸ run phase**, in the same breath as the stage check — ahead of `claim_for_launch` and well ahead of the worktree, so nothing is created and nothing is written.
- `start_pr_fix()` (`agents.py:702`) refuses too (it is the same work agent with a push), pointing at the member's own card. `start_review()` and `start_pr_review()` are allowed deliberately; `start_pr_review` now names `phase/<stem>` for a phase card, because its prompt asks GitHub for the diff by branch and `task/<stem>` was never cut.
- `manager/core/board.html` — `phaseFlight()` gained an `idle` reading, so an `in-progress/` phase card that has not been started (or was held) wears a `not started` / `held` pill in the settled `--idle` register with a line saying what **▸ run phase** would do. It is explicitly not work: no accent, no breathing mark, no caret, and a halt or a failed run still outranks it.
- `AGENTS.md` — both rules written down where the neighbouring behaviour is described.

**How it was verified** — `python3 -m unittest discover -s tests`, the project's whole definition of done: 1031 tests, passing. New file `tests/test_phase_card_refuses_work.py` (23 tests) covers the refusal and that it leaves no branch, worktree or agent record; that it precedes the claim (no `**Assignee:**` written) and survives a takeover; that an ordinary card starts work unchanged; that the two gates are mirror images; that a card retyped `**Type:** Phase` mid-run still reaps and lands in `review/`; each of the four agent kinds; and the card's four visual states through the page's own `phaseFlight()` under node.

**To do / to know**

- One existing test, `tests/test_phase_runs.py:517` (`test_running_it_again_clears_the_halt_and_carries_on`), is flaky — roughly one run in three it finds member 31 still in `in-progress/`. I reproduced it on the unmodified main checkout (`python3 -m unittest discover -s /Users/ronald/Dev/12v/ext/bench/tests -t /Users/ronald/Dev/12v/ext/bench -p test_phase_runs.py -k HaltNeverSkip`), so it is pre-existing and unrelated to this card. It deserves its own card; I have not touched it.
- `tests/test_phase_members_hidden.py:419` previously asserted that an `in-progress/` phase with no run "has nothing to say" — exactly the behaviour this card reverses. It is rewritten to the new rule rather than deleted, with a second case for `held`.
- Worth a reviewer's eye first: the decision that **↻ act on PR** refuses a phase card. Review feedback on a phase's PR now has no agent path — it goes to a member's card or to the phase branch by hand. That is a judgment call the task asked for explicitly, and the reasoning sits in the note at `manager/core/agents.py:151`.
- The unstarted card's pill depends on the runner's snapshot, which the phase beat publishes every `BOARD_PHASE_INTERVAL` (30s default). For up to one beat after a board start the card says nothing rather than guessing — the same latency as every other phase reading.
- The stray-run gap the task's Notes describe (a 0-byte log, no `run failed` pill) is untouched, as instructed.
