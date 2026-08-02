# 58 — A phase card will not move while its work is running

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/49
**Assignee:** istos
**Priority:** Medium — the hole is narrow but what falls through it is a
phase whose card and branch disagree about what is happening
**Type:** Feature

Dragging a phase card between stages while one of its members has an
agent in it is a move nobody can mean. Refuse it, name what to do — **‖
hold** stops the phase and the agent it has in flight — and let the move
through the moment nothing is running.

## Context

- Today a phase card is an ordinary card to `move_task()`. Nothing
  consults the phase, so a phase card can be walked back to `to-do/` (or
  archived, which is a move) while its second member is mid-run, leaving
  a card in one place and a live agent, a worktree and a phase branch in
  another.
- The stopping half already exists and does exactly the right thing.
  `stop_phase()` (`manager/core/phases.py:662`): *"the agent the phase has
  in flight is held exactly as its own card's hold would hold it"*, and
  the branch, the merges and the worktrees are left as they were. So the
  fix for a refusal is one action the person already has.
- A **halted** phase has nothing running by construction, so it moves
  freely — which is exactly when you would want to walk it back.
- The board has almost no refusals on drag. `move_task` raises for a bad
  stage or an existing file; the drag-to-`done/` sheet *intercepts* rather
  than refuses. This adds the first real "no", which is why the wording
  matters more than the check.

**Affected areas:** `manager/core/taskfiles.py` or
`manager/core/phases.py` for the check, `manager/core/httpd.py` where
`/api/move` and `/api/archive` are served, and `manager/core/board.html`
for how a refused drag looks.

## What to build

- **Refuse the move while any member has a live agent.** Not "while the
  phase is running" — a phase between members has nothing to lose, and
  refusing then would be a rule people learn to resent.
- **Say the whole thing in the refusal**: which member is working, and
  that **‖ hold** stops the phase and its agent without unwinding
  anything. A refusal that only says no makes the person guess, and the
  guess is usually to force it.
- **Archiving is a move.** `archive_task()` takes the same guard, or the
  rule has a hole shaped exactly like the tidiest thing to do with a
  phase you have lost interest in.
- **The card returns to where it was**, cleanly, with the reason in the
  toast — a drag that snaps back with no explanation is worse than one
  that is not allowed to start.
- **Guard the server, not just the drag.** The board can be stale, and
  `/api/move` is reachable regardless — the same lesson card 54 wrote
  down about `▸ start work` on a phase card.

**Out of scope** — tempting neighbours left alone:

- Stopping members individually. `‖ hold` on the phase covers it, and a
  per-member stop is card 57's lane doing ordinary card things.
- Refusing to move a *member* card. That is a person overriding the
  runner deliberately, and the phase halting on it afterwards is the
  honest outcome.
- Anything about what the move does once allowed.

## Acceptance

- [ ] Given a phase with an agent running on a member, when its card is
      dragged to another stage, then the move is refused, the card stays,
      and the message names the working member and **‖ hold**.
- [ ] The same phase card cannot be archived while that agent runs.
- [ ] Given the phase is held, the card moves — and archives — normally.
- [ ] Given the phase has halted, the card moves normally: nothing is
      running.
- [ ] Given a phase between members, with nothing launched, the card
      moves normally.
- [ ] `POST /api/move` on that card is refused with the same reason, from
      a stale page or by hand.
- [ ] An ordinary card is unaffected in every case.
- [ ] Edge case: a member's agent that has died but not been reaped does
      not lock the phase card forever — the check reads what is actually
      running, not what was once started.

## Notes

The rule earns its keep on the archive path more than the drag: a phase
you have given up on is precisely the one you would archive, and doing it
mid-run would leave a branch, a worktree and a running agent belonging to
a card that is no longer on the board.

Worth phrasing the refusal as an instruction rather than a prohibition —
"hold the phase first, then move it" reads as help, and "cannot be moved
while running" reads as a wall. The board's other refusals already do
this: work starts from `in-progress/` *— move the card there first*.


---

## Work report — 2026-08-02 09:23 (Skye)

Done — committed on `task/58-a-phase-card-will-not-move-while-its-work-runs`, full suite green.

**WORK REPORT**

A phase card is now refused a move while any of its members has a live agent, on the server, with a refusal that names the working member and **‖ hold**. Committed as `8fbfc10`; `python3 -m unittest discover -s tests` passes (966 tests, 26 of them new).

**What changed**

- `manager/core/phases.py` — new `assert_not_working(filename, doing)`. It reads the one task file first (an ordinary card is answered in one `read_task` and never reaches the phase logic), then resolves the phase's `## Cards` list to member files and asks what is actually running. The refusal reads, verbatim: *"Ship the site is still working — Juno is on 31 — Stand up site/. ‖ hold stops the phase and the agent it has in flight, and leaves the phase branch, everything merged into it and every worktree exactly as they are. Hold it first, then move the card."* (`archive it` / `merge it` close the other two callers.) The old private `_agents_on` is gone; `stop_phase` reads the new shared helper.
- `manager/core/agents.py` — `working_on(files)` plus `_alive(record)`: liveness is asked of the process (`proc.poll()`), not of the registry's `status`, which the reaper flips a moment after a run ends. That is the acceptance list's "died but not reaped" edge — a rule that only refuses must not hold a card hostage to a run that is already gone. The runner's own `_running_on` is deliberately left conservative, since it gates *acting* on a card rather than refusing.
- `manager/core/httpd.py` — the guard runs before the write on `/api/move`, `/api/archive` and `/api/task/complete`.
- `manager/core/board.html` — no copy of the rule in the page; the existing `rawMove`/`archiveCard` failure paths already toast the server's words and reload, so the card stays put with the reason on screen. What did change is the toast itself: bounded to the viewport, wrapping (rounded rect over pill past 90 characters), and up for as long as its text takes to read (capped at 9s). A refusal you cannot finish reading is the wall this card was written against.
- `AGENTS.md` — a paragraph in "Watching one run, and watching it stop" recording the rule and its edges.
- `tests/test_phase_card_moves.py` — the refusal and its wording (pinned verbatim), archiving, the held/halted/between-members cases that must still move, ordinary and member cards, the unreaped-death case, `POST /api/move` and `/api/archive` through a real `ThreadingHTTPServer`, and one end-to-end case on the `test_phase_runs` harness: a real phase, a real member launched on a sleeping adapter, refused, then `‖ hold` and the card moves.

**For the reviewer, in order**

1. Two judgement calls worth confirming: I guarded `/api/task/complete` as well as the two endpoints the task names — merge & clean up ends in a move to `done/`, and doing it with a member still working would take the phase branch to `main` without that member's work. Drop that line if you read it as out of scope.
2. Dragging a phase card with a branch or PR to `done/` still opens the merge-and-clean-up sheet first (that interception is client-side); the refusal lands on whichever button you then press. Pre-empting it would mean a second copy of the rule in the page, which is why I left it.
3. `assert_not_working` does not take the runner's `_LOCK` — a beat can sit in a 180s merge, and a move that waited on one would be a worse answer than the sliver it closes. The reasoning is in its docstring.
