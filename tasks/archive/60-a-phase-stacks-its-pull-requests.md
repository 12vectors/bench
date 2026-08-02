# 60 — A phase stacks its pull requests

**Status:** Archived
**Priority:** Medium — it changes what a phase hands you at the end, which
is the only part of a phase a person actually has to do
**Type:** Feature

A phase currently hands you one pull request containing everything it did.
Phase 53's was +807 −7 across six files for two cards, and the only way to
review one card of it is to go and find that card's own PR, which is
already merged. Stack them instead: each member's pull request stays open,
based on the one before it, reviewable and mergeable in order.

## Context

- **What happens today.** Each member branches from the phase branch's tip
  and opens a PR against the phase branch (`github.pr_base()` — "the only
  base whose diff is the member's own work"). When CI passes, the runner
  **merges it straight into the phase branch**, which closes that PR. At
  the end one PR goes from the phase branch into `main`. Phase 53 is the
  worked example: PRs #44 and #45 merged into `phase/53-…` before anyone
  looked at them, and #46 is the +807 line result.
- So the per-card diffs *exist* but are gone by the time you review, and
  what survives is the aggregate. The stack is being flattened as it is
  built.
- **Nothing is reviewable in the middle.** Because members are merged on
  CI-green, there is no point at which a person can reject card two and
  keep card one. Rejecting means unwinding a merge the board made.
- GitHub's own support: <https://docs.github.com/en/pull-requests/how-tos/stacked-pull-requests>
  — "break large code changes into a chain of smaller, dependent pull
  requests you can review and merge independently". The quickstart uses a
  `gh stack` extension, **which is not installed here** (`gh stack` is an
  unknown command on this machine), so the first job is finding out what
  the plain API and `gh` can do without it.
- The design exploration for phases, including where the controls live:
  https://claude.ai/code/artifact/727a3b64-1354-4fb0-a73d-b700bdfc2b19

**Affected areas:** `manager/core/phases.py` (what the runner does when a
member goes green), `manager/core/github.py` (`pr_base`, `branch_of`,
what gets opened and closed), and whatever the Phases view shows about a
member (card 57).

## What to build

- **Stop merging members during the run.** A member that goes green stays
  an open PR. That is the whole change in one sentence, and everything
  else follows from it.
- **Chain the branches.** Member *n* branches from member *n−1*'s branch
  rather than from a phase tip that has been merged into — so its diff is
  still only its own work, and its PR is based on the PR below it.
- **The phase card sits on top of the stack.** Its PR is what lands the
  whole thing in `main`; the members' PRs land into each other.
- **Nothing merges without a person, again.** This removes the one place
  the board merges anything: no more merging into a branch it owns. Say
  so in `AGENTS.md`, which card 49 already had to qualify.
- **Show the stack.** A member's card should say where in the chain it
  sits and what it is based on; the Phases view lane is the natural place
  for the whole chain to be legible at once.
- **Find out what GitHub gives us for free.** Whether merging the bottom
  PR auto-retargets the next one, whether a stack can be merged in one
  action, and whether any of it needs the `gh stack` extension the docs
  mention. Answer that before designing around it — an assumption here is
  expensive.

**Out of scope** — tempting neighbours left alone:

- Rebasing anything. bench resolves conflicts additively and never
  rebases or force-pushes, and stacks are usually kept tidy by rebasing.
  That collision is the risk, not the work — see Notes.
- Reordering a phase's list once it is running.
- Card 59's sweep, which still applies: when the top of the stack lands,
  the members are done.

## Acceptance

- [ ] Given a phase of three cards, when it runs to completion, then
      three member PRs are **open** and each is based on the one before
      it, with the first based on `main`.
- [ ] Each member's PR diff contains only that member's work.
- [ ] The phase card's PR carries the whole stack into `main`.
- [ ] The runner merges nothing during a run — no branch is merged by the
      board.
- [ ] Given the bottom PR is merged by a person, the next one is still
      reviewable and still shows only its own work.
- [ ] Given a member's PR gets changes requested, `↻ act on PR` still
      works on it and the members above it are not broken by the update.
- [ ] The Phases view shows the chain in order and what each PR is based
      on.
- [ ] Edge case: a phase of exactly one card produces one PR, not a stack
      of one plus a redundant phase PR.

## Open questions

- **How does the stack land?** Merging bottom-up means one click per
  card, which is the thing phases exist to avoid — but merging only the
  phase PR at the top would merge every member's commits into `main`
  while leaving their PRs open and stale. Which of those is right, and
  whether GitHub's stacked support closes the lower PRs automatically
  when the top merges, decides the shape of the whole card.

## Notes

**The rebase problem is the real risk.** Stacked PRs are normally kept
straight by rebasing the branches above whichever one changed, and this
project has a hard rule against that: conflicts are resolved additively,
in a dedicated merge commit, and nothing force-pushes. A stack maintained
by merging downward instead of rebasing upward is legal here but produces
a messier history than the stacked-PR workflow assumes. Worth deciding
early whether that is acceptable or whether the rule bends for branches
that only ever existed inside one phase.

The prize is worth the trouble: today a phase's review is one large diff
that arrives after every constituent decision has already been merged.
Stacking makes the phase's own structure visible at the moment of review —
which is exactly when the sequencing the phase was built around is worth
seeing.
