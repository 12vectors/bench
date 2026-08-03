# 62 — Running a halted phase again resumes the member it stopped on

**Status:** Backlog
**Priority:** High — a board that dies mid-run halts its phase on a member
it can never get past, and the way out is a two-step nobody is told about
**Type:** Bug

**▸ run phase** on a halted phase is meant to mean "try that member
again" — `_this_run()` says so in its own docstring, and scopes the log
so that it does. It does not, for the one member it matters most for: a
member whose run died left a branch behind, and `_member_state()` reads
any branch as evidence the phase already dealt with it. So the re-run
recomputes, sees a card in `in-progress/` with no agent on it, and halts
on the same member with the same sentence — once a beat, for as many
times as the button is pressed. The phase cannot be restarted from the
board at all; it can only be restarted by launching the stuck member's
work agent by hand first, which is a step the board never mentions.

## Context

A board hosted by a shell that got killed took its member's agent down
with it. On restart the phase halted at that member — correct, nothing
was running — and every **▸ run phase** after that appended a
`run started` line and halted again about a minute later. What actually
cleared it was **▸ start work** on the member, and *then* **▸ run
phase**: with an agent alive on the card, `_running_on()` short-circuits
at the top of `_member_state()` and the runner waits instead of halting.

- `manager/core/phases.py:144-155` — `_this_run()` scopes the log to the
  entries after the last `run started`, and says why: "a member whose run
  died is launchable again, which is exactly what asking for the run
  again meant." The intent is already written down. It is `_started()`
  that this feeds, and only `_started()`.
- `manager/core/phases.py:263-265` — the clause that defeats it:
  `if number not in started and not has_branch: return "pending"`. A run
  that died always leaves `task/<stem>` behind — the branch is cut at
  launch, before the agent does anything — so `has_branch` is true and
  the member never reads as pending, however many times the run is
  restarted.
- `manager/core/phases.py:278-281` — where it lands instead: stage is
  `in-progress`, so `halt`, "its run ended without reaching review/".
  True, and the end of the conversation: the sentence names no way out.
- `manager/core/phases.py:220-230` — `_failure_note()` is empty after a
  restart, by design ("a restart forgets"). So the halt a killed board
  produces is also the halt with the least to say, which is the wrong way
  round.
- `manager/core/agents.py:391-402` — and the reason this is safe to fix:
  `start_agent()` does **not** refuse an existing worktree. It continues
  in it, on the same branch, and says so ("is back on … — continuing
  branch …"). Relaunching a member whose run died is an operation the
  board already supports everywhere except here.
- `AGENTS.md`, "Agents working the board" — says "a work agent's worktree
  must not already exist when it starts". That is not what the code does
  (above), and it is precisely the sentence that would talk someone out
  of this fix. It needs correcting in the same change.

**Affected areas:** `manager/core/phases.py` — `_member_state()` and the
halt text; `AGENTS.md` for the two sentences that describe both.

## What to build

- **Let a new run resume a member it did not start.** A member that is
  not settled, not running, and not started *by this run* is the phase's
  next piece of work whether or not a branch exists for it — that is what
  `_this_run()`'s scoping already means, and `has_branch` is answering a
  different question than the one it is asked here. Keep the branch check
  where it earns its keep: deciding `merged`, where containment is the
  whole point.
- **Resume rather than restart.** `_launch()` should reach a member with
  a branch and a worktree already there and let `start_agent()` continue
  in it, so a partially-done run keeps its commits. Nothing here should
  delete a worktree or a branch to make room for itself.
- **Say what the halt means when it is still a halt.** A member that
  halts inside the run that started it is stopped for a reason and stays
  stopped — but the sentence should name the way through the way every
  other refusal on this board does: that running the phase again resumes
  it, and that **‖ hold** is the other answer.
- **Cover the sequence the board actually produced**: run starts, member
  launches, the run record disappears (a killed board), the phase halts,
  the phase is run again — and the member comes back up rather than
  halting a second time.

**Out of scope** — near neighbours this deliberately leaves alone:

- **The five halting conditions.** `NOT READY`, a non-zero exit, a clean
  exit with no commits, CI red and a non-mechanical merge are all still
  halts, still within the run that hit them, and still nothing that
  retries by itself. This changes what a *new* run may do, which was
  always a person's decision.
- **Why the agent died with the board.** A board in a foreground shell
  taking its agents down with it is real and is not this card — this one
  is about getting the phase moving again afterwards.
- **Anything auto-retrying.** No loop, no backoff, no second attempt the
  person did not ask for.

## Acceptance

- [ ] Given a phase halted at a member sitting in `in-progress/` with a
      branch and no live agent, when the phase is run again, then that
      member is launched and the phase continues — no second halt.
- [ ] Given that member's worktree still has the dead run's commits in
      it, when it is relaunched, then the commits are still there and the
      agent continues on the same branch.
- [ ] Given a member that halted for one of the five conditions *within
      the current run*, when the beat passes again, then it stays halted
      and nothing relaunches.
- [ ] Given a member already merged into the phase branch, when the phase
      is run again, then it is not relaunched — containment still decides
      `merged`.
- [ ] The halt line for a member that ended without reaching `review/`
      names running the phase again as the way to resume it.
- [ ] Edge case: a member with a branch that nobody in this run started
      and that has no commits on it at all — the empty-branch case a
      broken launch leaves — still does not read as merged.
- [ ] `AGENTS.md` no longer says a work agent's worktree must not exist,
      and says what a re-run does to a halted member.

## Open questions

- None.

## Notes

The board was right about everything except what to do next: the halt was
accurate, the log was complete, and the Phases view showed the member it
stopped on. What was missing was that the one control offered — run it
again — could not move it, and the thing that could was not offered.

**Risks** — the `has_branch` clause is load-bearing for `merged` and only
wrong for `pending`; a change that removes it from both would make a
merged member eligible to relaunch, which is the one outcome worse than
halting. The acceptance list pins both sides on purpose.
