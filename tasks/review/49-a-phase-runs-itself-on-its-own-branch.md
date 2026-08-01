# 49 — A phase runs itself, on a branch of its own

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/41
**Assignee:** istos
**Priority:** High — the point of the whole thing: work that continues
without you until something is genuinely wrong
**Type:** Feature
**Depends on:** 48 — the board has to know what a phase is first

Give a phase its own integration branch and work the list into it: each
card branched from the phase's tip, run headless, merged back when its
checks are green, and the next one started. One PR at the end, into
`main`, for a human. The runner is a beat on the board, not an agent —
everything it decides is already structured state, and an agent paid to
poll would be the wrong tool at the wrong price.

## Context

The problem this exists to solve, precisely:

- `_fresh_branch_point()` (`manager/core/agents.py:167`) branches every
  new task worktree from `origin/main`. For *related* tasks — the whole
  premise of a phase — card two branched from main cannot see card one's
  work while card one sits unmerged in `review/`. It will conflict, or
  quietly build the same thing twice.
- Gating on a merge into `main` would fix the branch point and destroy
  the point: `main` is merged by a person, so the phase would stall on
  every card.

So the phase gets **its own branch**, and the human gate moves from every
card to the phase boundary. The promise bench makes — *nothing merges
without you* — is about `main`, and it survives intact: the board merges
into a branch it created, inside a scope you opened, and `main` still
waits for your click.

What already exists and should be used rather than rebuilt:

- The board reacts to state without an agent: a card entering `review/`
  opens a PR "mechanically, by the board", and the PR poller is a plain
  thread checking reviews, CI and mergeable state every 60s.
- `github.public_state()` (`:378`) exposes `{verdict, ci, copilot,
  conflicts, url}` per card — the advance condition is readable, not
  judged.
- `watch.py` answers *am I the actor?*, and `AGENTS.md` says every future
  automation hung off a stage transition inherits it.
- Every failure mode a card can have is already a state it wears:
  `NOT READY`, `run failed`, a clean exit that committed nothing.
- `complete_task()` (`manager/core/github.py:387`) is the model for a
  careful multi-step git operation that narrates and aborts cleanly.

**Affected areas:** a new `manager/core/phases.py` — right of `agents` in
the module map, since it needs `taskfiles`, `github` and `agents` — plus
the branch-point change in `agents.py` and a beat wired in `board.py`.

## What to build

- **A phase branch.** Starting a phase cuts `phase/<task-stem>` from the
  newest `origin/main` it can see, by the same rule and the same timeout
  a task branch uses.
- **Members branch from the phase tip**, not from main. This is the one
  change inside `agents.py`: where a launch is part of a phase, the
  branch point is the phase branch rather than `origin/main`, and the
  ticker names it as it already names an unusual branch point.
- **A stateless beat.** On each pass, recompute: which members are
  finished, which is first unfinished, what does it need. Hold no
  registry — a board restart then resumes a phase by looking, and the
  same logic answers "what now?" whether the last event was a launch, a
  merge or a crash.
- **Advance on green.** A member is finished when its card is in
  `review/` and its CI has passed. Then merge its branch into the phase
  branch — additively, never rebasing, never force-pushing — and start
  the next member whose `Depends on` are all finished.
- **Halt, never skip.** The five conditions, each already a visible state
  on the card: `NOT READY`; a non-zero exit; a clean exit with no
  commits; CI red; a merge into the phase branch that is not mechanical.
  A phase that steps over a failed card builds the rest on a foundation
  that never landed.
- **Keep the phase branch fresh.** Merge `main` into it on the beat,
  additively, so a phase that runs for hours does not drift into one
  enormous conflict at the end. A conflict there halts the phase like any
  other.
- **Finish into a PR.** When every member is finished, push the phase
  branch, open one PR into `main` with the member list as its body, write
  the `**PR:**` line into the phase card, and move the phase card to
  `review/`. From there the existing apparatus applies unchanged — CI
  chip, `◔ review PR`, `⚑ copilot`, and drag-to-`done/` for **merge &
  clean up**.
- **One board runs it.** The actor rule decides; a replica renders the
  phase and advances nothing.

**Out of scope** — tempting neighbours left alone:

- Running members in parallel where dependencies allow. Sequential
  first; parallelism is a second card once the sequencing is trusted.
- Any UI. The header chip, the run and hold actions and the narration
  are card 50 — this card is reachable through the API and the ticker.
- Auto-merging anything into `main`, under any condition.
- Nested phases, and a member belonging to two phases at once (48 flags
  that as drift; here it simply must not run twice).

## Acceptance

- [ ] Given a phase whose members are unstarted, when it is run, then
      `phase/<stem>` exists at the newest `origin/main`, and the first
      member's worktree is branched from it.
- [ ] Given member one is in `review/` with CI green, when the beat runs,
      then its branch is merged into the phase branch and member two is
      launched from the new tip — and member two's worktree contains
      member one's work.
- [ ] Given a member whose `Depends on` names an unfinished card, it is
      not launched even when the list reaches it.
- [ ] Given a member that exits `NOT READY`, the phase halts, the card
      walks back as it does today, and no further member starts.
- [ ] Given a member whose run fails, or exits clean with no commits, or
      whose CI is red, the phase halts in each case.
- [ ] When every member is finished, one PR is opened from the phase
      branch into `main`, the phase card carries its `**PR:**` line, and
      the card is in `review/`.
- [ ] Restarting the board mid-phase resumes it without repeating a
      launch or a merge.
- [ ] With `BOARD_SYNC` on, a second board shows the phase advancing and
      launches nothing itself.
- [ ] Edge case: a phase whose list is empty goes straight to `review/`
      with nothing to merge, or refuses to start — either, but not a
      branch left behind and a card stuck in `in-progress/`.
- [ ] Edge case: a member card moved by hand while the phase is running
      does not cause a second launch on the same task.

## Notes

**`AGENTS.md` will need a sentence it does not have.** "Merging remains
yours — the board never merges" is quoted as an absolute. After this it
needs to say what it always meant: the board never merges into `main`. A
phase branch is the board's own, and merging into it is bookkeeping in
the same family as committing a move.

A lever worth considering once this works: run `◔ review PR` on each
member and require `APPROVE` as well as CI green, with one automatic
`↻ act on PR` round before halting. That would put an agent's review
inside the phase and keep the human's for the boundary — more autonomy,
more tokens, and a bigger change than this card should carry.

**Risks** — this is the third place in the codebase that merges git
branches, after `complete_task` and `↻ act on PR`'s conflict resolution.
It must behave like both: abort cleanly, leave no half-merged branch, and
narrate every step. A phase branch that ends up in a broken state is
worse than a phase that refuses to start.


---

## Work report — 2026-08-01 09:44 (Reed)

The work is committed and the full suite is green.

**Work report**

A phase now runs itself on its own branch: the feature is implemented, tested and committed on `task/49-a-phase-runs-itself-on-its-own-branch` in four reviewable commits, with `python3 -m unittest` passing at 736 tests (59 of them new, in `tests/test_phase_runs.py`). Nothing is blocked.

**What to look at first**

- `manager/core/phases.py` — the new module, and the whole of the runner.
- Two decisions the card left open that I made and would flag for you: **(1)** a `## Phase log` section on the phase card is where the runner writes down what it has already started, because nothing else on disk can tell "this member has run and it ended badly" from "the phase has not reached it yet" — without it a restarted board would relaunch a run that died. **(2)** a phase member's PR is opened against its phase's branch rather than `main` (`manager/core/github.py`, `_pr_base`), since a member PR into `main` would carry the whole phase and invite exactly the merge this design refuses to make.
- `AGENTS.md` and `site/templates/home.html` both said "the board never merges" as an absolute; both now say "never merges into `main`", which is what it always meant.

**What it does**

Starting a phase (`POST /api/phase/run`, or `phases.start_phase`) cuts `phase/<stem>` from the newest main it can see, by the same rule and timeout a task branch uses, and gives it a worktree. Members branch from the phase's tip — the one change inside `agents.py` — run as ordinary headless work agents, and are merged back when their card reaches `review/` and its checks are not against it. Merges are additive throughout; `main` is merged into the phase branch on every beat; a conflict aborts, leaves the branch as it was, and halts naming the colliding files. When every member is in, the branch is pushed, one PR into `main` is opened with the member list as its body, the `**PR:**` line is written and the card moves to `review/`.

The beat (`BOARD_PHASE_INTERVAL`, 30s) recomputes everything each pass and holds no registry: git answers what has merged (branch containment), the phase log answers what has been started. Halting is written once and then held; running the phase again is what clears it, and the run is scoped to its own log line so a member whose run died is launchable again. The actor rule is decided by the phase card's `**Assignee:**`, so a replica renders the phase and advances nothing.

**Two rules worth your judgement**

- **CI green means "not against it".** Red halts, running waits, and a member with no checks at all advances — otherwise a project without CI would deadlock every phase it runs.
- **Merged member cards stay in `review/`.** Their work is in the phase branch, not in `main`, so calling them `done/` would be a claim the board cannot make; they settle when you merge the phase PR.

Out of scope as the card asked: no parallelism, no UI (the header chip and card actions are card 50), and nothing merges into `main` under any condition.


---

## PR update — 2026-08-01 10:03 (Reed)

ADDRESSED: Fixed the three Copilot review points — no-identity beat gating, unwritable phase log, and remote-only phase branch as a member PR base — with four new tests; full suite green (740).

The work is committed on `task/49-a-phase-runs-itself-on-its-own-branch` (commit `1f8f5f6`) and pushed, so PR #41 is updated; `python3 -m unittest discover -s tests` passes at 740 tests (4 new). The PR reported `MERGEABLE`, so there was no conflict to resolve. All three review comments came from Copilot; each was a real robustness gap on a path the normal single-board flow never takes, and each is addressed.

**Review points and what I did:**

- **`_mine()` strands a phase when the checkout has no git name** (`manager/core/phases.py`) — Copilot: in team mode `_mine()` returned `False` when `taskfiles.actor_name()` is empty, yet `agents.claim_for_launch()` still permits the launch there, so a phase could start (branch cut, run recorded) and then advance nowhere. **Fixed:** `_mine()` now treats "no local identity" as the lone actor and returns `True`, matching the launch gate it mirrors. Added two unit tests (no-identity is not gated; a named non-assignee still is).

- **`_record()` ignored whether the log line was written** (`manager/core/phases.py`) — Copilot: if the phase log can't be written the runner could still launch or merge, leaving no durable record and inviting a repeated launch/merge after a restart; treat it as a hard halt without relying on writing the log again. **Fixed:** split out a best-effort `_write_log()`; `_record()` now raises `_Halt` when the write fails, *before* the action it precedes; `_halt()` and `_start()` use `_write_log()` directly so recording a halt (or reporting a failed start via `ValueError`) can never itself raise. Added a test that a member is never launched when its "started" line won't write.

- **`_pr_base()` only recognized a *local* phase branch** (`manager/core/github.py`) — Copilot: a board that didn't run the phase knows the phase branch only through the remote (sync fetches `origin/main` and nothing else), so a member PR could wrongly base on `main`. **Fixed:** `_pr_base()` now also honours a phase branch the remote carries (`git ls-remote`, only consulted when the local branch is absent), and `_open_pr()` only pushes the base when it is a local branch (the remote already has it otherwise). Added a test that `_pr_base` returns the phase branch when only the remote carries it.

One thing worth knowing (no action needed): the specific cross-board scenario Copilot sketched for point three is not actually reachable today — `_open_pr()` bails out earlier because a non-actor board also lacks the member's local `task/<stem>` branch. I still made the change: it's cheap, it removes a latent bug (a base returned but not pushable), and it makes the "member PRs are based on the phase branch" invariant hold regardless of which board opens the PR.
