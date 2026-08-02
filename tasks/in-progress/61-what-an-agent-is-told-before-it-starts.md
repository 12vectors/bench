# 61 — Phase: what an agent is told before it starts

**Status:** In Progress
**Assignee:** istos
**Priority:** High — every card in it is a hole that has already cost a
run, and two of them cost one this week
**Type:** Phase

Three cards about the moment a headless agent is launched: what it is
allowed to run, what it is told about the run it is in, and what the
board must refuse to launch at all. They are separate defects with one
shape — the launch path assumes the agent knows things nobody tells it.

## Cards

- 55 — A headless run gets one turn, and nothing tells the agent that
- 54 — A work agent must refuse a phase card
- 46 — A board whose agents cannot run anything says so

## Why this order

None of the three depends on another, so none carries a
`**Depends on:**` line. The order is editorial and file-shaped:

**55 first** because it touches only the prompt templates and their
contract test — no overlap with anything, and it is the cheapest to get
out of the way. **54 and 46 adjacent** because both edit
`manager/core/board.html`, 54 to distinguish a phase card that has not
been started and 46 to say when a board's agents have no commands to run.
Sequencing them means the second starts from a worktree that already has
the first, rather than colliding in the file this repo edits most.

## What done looks like

- A work prompt that states plainly that the run is one non-interactive
  turn and that uncommitted work dies with it — the sentence that would
  have saved card 47's run.
- `/api/agent/start` refuses a phase card, from a stale tab or by hand,
  and says **▸ run phase** instead.
- A board with `BOARD_AGENT_COMMANDS` empty says so once, where a person
  will see it, instead of letting an agent discover it four minutes into
  a run.

## Notes

**This is also the first real exercise of the Phases view.** Cards 56–59
landed after phase 53 finished, so nothing has yet been watched through
the new arrangement: members should disappear from the Board and appear
in a lane, the column counts should describe only what is visible, the
phase card should refuse to move while a member is running, and
**merge & clean up** should sweep the cards *and* their worktrees and
branches. Worth watching this one rather than walking away from it, for
that as much as for the work.

**One thing that looks alarming and is not.** Card 54 changes
`agents.start_agent`, which is the function the phase runner itself calls
to launch each member — including the members after it. It cannot break
the run that carries it: the board runs from the main checkout, and a
member's changes live in its own worktree until the phase's PR is merged.
The same is true of 55's prompt edits, which are read fresh from the main
checkout on every launch.

Card 46's own card notes the trade it comes from: detection replaced the
install question, so a project the detector does not recognise starts
with an empty allowlist. That is the honest outcome, and this is the
card that stops it being a silent one.

## Phase log

- 2026-08-02 12:35 · run started on phase/61-what-an-agent-is-told-before-it-starts
- 2026-08-02 12:35 · 55 started
- 2026-08-02 12:51 · 55 merged into phase/61-what-an-agent-is-told-before-it-starts
- 2026-08-02 12:51 · 54 started
- 2026-08-02 13:33 · 54 merged into phase/61-what-an-agent-is-told-before-it-starts
- 2026-08-02 13:33 · 46 started
- 2026-08-02 13:43 · 46 merged into phase/61-what-an-agent-is-told-before-it-starts

