# 63 — A phase in flight is work happening, and the board should say so without an agent

**Status:** Backlog
**Priority:** High — a phase advancing between members reads as an idle
board on every surface keyed to a running agent, including the one surface
a backgrounded tab has left
**Type:** Bug

The board has one word for "something is happening" and it is spelled
"an agent is running". A phase does not need one: it advances on a beat,
and it spends much of its life with no agent alive at all — while a
member's checks run, between a merge and the next launch, while a
dependency lands. In all of those the phase is working and every
agent-keyed signal on the board says nothing is. The tab title goes back
to the plain project name, the live chip falls through to "you, working
1 session", and the member the phase is actually waiting on wears
`waiting on you` — which is the opposite of true, because nothing is
waiting on you and the phase will merge it by itself the moment CI goes
green.

## Context

Observed on a phase with 11 of 14 merged, member #31 in `review/` with
`CI ◌` in flight and the phase chip reading
`⟶ Phase: platform harde… 11/14 · on #31`. The phase was healthy and
advancing; nothing but that chip said so.

- `manager/core/board.html:1225` — `tabTitle(S.state.project, S.view,
  runningAgents().length)`. The count is agents and only agents, so a
  phase in flight with no agent produces the quiet title, byte for byte.
  This is the surface that matters most: the comment above it argues the
  count leads *because* "a backgrounded window" is the only place the
  state can still be read — and a phase is exactly the long-running thing
  you background the tab on.
- `manager/core/board.html:1026-1028` — `runningAgents()`, described as
  "one reading of 'an agent is working', shared by the header chip and
  the tab title, so the two can never disagree". Correct as far as it
  goes; there is no equivalent reading of "the board is working".
- `manager/core/board.html:1099-1104` — `phasesInFlight()` already exists
  and already answers it: `p.running || p.halted`, straight off the
  snapshot. The title has the fact available and does not consult it.
- `manager/core/board.html:766-768` — `pillFor(stage, working)`: `working`
  is agent-driven, and every other `review/` card is `waiting on you`
  unconditionally. A phase member whose checks are still running is not
  waiting on anyone.
- `manager/core/phases.py:286-322` — the snapshot the UI reads carries all
  of it already: `running`, `halted`, `stopped`, and per-member states
  including `waiting` ("its checks are still running") and `ready`. No new
  server state is needed for any of this.
- `AGENTS.md`, "Watching one run, and watching it stop" — "Only a run
  wears the working vocabulary … because only a run is work happening."
  That sentence is the source of the gap: a phase run *is* a run. It
  needs saying in a way that does not read as "only an agent".

**Affected areas:** `manager/core/board.html` — the header, the tab title
and `pillFor`; `AGENTS.md` for the working-vocabulary rule and the tab
title's description.

## What to build

- **The tab title says a phase is in flight.** A board with a phase
  running and no agent on anything must not render the quiet title.
  Reuse `phasesInFlight()` rather than adding a second reading of it.
- **Without ever implying an agent that is not there.** The count is a
  count of agents and stays one — `1◌` when a phase runs and nothing is
  launched would be a lie, and the tab is the one place nobody can check
  it against the board. Use the phase's own glyph (`⟶`, as the chip and
  the member chips already do) so the two states are distinguishable at a
  glance and can appear together.
- **A halted or held phase wears none of it.** A halt is stopped work and
  already says so in `--alarm` in the chip, the switcher and a toast;
  putting it in the working position of the title would make "something
  is happening" mean "something has stopped".
- **The member the phase is waiting on stops claiming to wait on you.**
  A `review/` card whose phase is running and whose checks are still
  running should read as what it is — the phase's business, not yours.
  It keeps its CI chip; this is the pill only.
- **Say the rule properly in `AGENTS.md`.** The working vocabulary
  belongs to work in flight, and a phase beat is work in flight; the tab
  title's paragraph should say what it leads with when the work is a
  phase.

**Out of scope** — nearby and deliberately untouched:

- **The header's phase chip.** It is already correct and already the
  fullest statement of this on the screen. This card is about the
  surfaces that contradict it.
- **The breathing animation's meaning.** One looping animation, one
  meaning — work in flight. Nothing here adds a second animated state.
- **Any new server-side state or event.** The snapshot already carries
  every fact needed; a change to `phases.py` would be a sign of solving
  it in the wrong layer.
- **What a phase member's pill says in every other situation.** Only the
  running-phase, checks-running case is wrong today.

## Acceptance

- [ ] Given a phase running with a member in `review/` whose CI is still
      running and no agent alive anywhere, when the tab is backgrounded,
      then the title says work is in flight.
- [ ] Given the same board, the title does not state an agent count — a
      reader cannot conclude an agent is running when none is.
- [ ] Given both an agent running and a phase in flight, the title says
      both, and still fits a narrow tab well enough to read the leading
      mark.
- [ ] Given a phase that has halted, the title carries no working mark;
      given a phase held with **‖ hold**, likewise.
- [ ] Given a board with no phase and no agent, the title is exactly the
      plain `<project> · bench` it is today, byte for byte.
- [ ] Given a member in `review/` whose phase is running and whose checks
      are still running, its pill does not say `waiting on you`.
- [ ] Given a member in `review/` whose phase has halted, or a `review/`
      card in no phase at all, the pill is unchanged.
- [ ] Edge case: two phases in flight at once — the title says work is
      happening once, not twice, and does not grow with the number of
      phases.

## Open questions

- None.

## Notes

The confusion this fixes is specific and worth keeping in mind while
choosing the wording: the board was not wrong anywhere, it was *silent*
in the places a person looks when they are not looking at the board. The
Phases view said 11 of 14 and named the member in flight; the header chip
breathed. Both require the tab to be in front of you, which is the state
a phase is least likely to be in — a phase is the feature you start and
walk away from, and walking away is when the tab title becomes the whole
interface.

**Risks** — the tab title is read at a glance and truncates hard, so the
temptation is to pack it. Every character added to the lead costs the
project name, which is what tells two bench tabs apart. Prefer one glyph
over a word.
