# 40 — A card with open questions refuses the launch, not just the agent

**Status:** Archived
**Priority:** High — the board documents this as a gate and implements it
as a suggestion; every miss spends a worktree, a branch and a full agent
run on a task nobody could answer
**Type:** Bug

`AGENTS.md` promises that "a non-empty Open-questions section makes an
agent refuse the task (`NOT READY`) rather than guess", and the task
template calls the section load-bearing. Nothing in the board enforces it.
The whole decision is a paragraph in the work prompt, judged by the model
after the launch has already built the worktree — so a card can be worked
with its questions still open, and was.

## Context

What actually decides this today:

- `manager/core/prompts/work.md:16-27` — the only implementation. It asks
  the agent to read the task critically and, if it finds open questions
  "that only the task's author can settle", to make no edits and reply
  with `NOT READY: <reason>`. It then narrows itself: "Only questions
  that change what should be built count — implementation details you can
  decide yourself by reading the codebase and AGENTS.md do not."
- `manager/core/agents.py:229` — `start_agent()` refuses a launch from
  the wrong stage (`_validate`), refuses a card someone else holds
  (`_claim_for_launch`), and refuses a worktree on the wrong branch. It
  never reads the task's content. By the time the prompt's check runs,
  the worktree exists, the branch exists, and the run is billed.
- `manager/core/agents.py:340-345` — the `NOT READY:` marker is parsed
  out of the agent's final output, and the board then walks the card back
  and deletes the untouched worktree and branch. That machinery is fine;
  it is the trigger that is soft.

The miss that prompted this card: **#32** ("Serve bench.12vectors.com from
a Cloudflare Worker") carried one Open question — manual `wrangler deploy`
versus a GitHub Action — and an agent started work on it anyway. That is a
defensible reading of the prompt: the entry ends with
`Recommendation: manual for v1`, and a question that carries its own
answer does not "change what should be built". So the card is two bugs
wearing one shirt — a gate that is only advisory, and an authoring
convention that lets an already-decided question sit in the section that
is supposed to stop the work.

**Affected areas:** `manager/core/agents.py` (the launch guard),
`manager/core/taskfiles.py` (reading the section),
`manager/core/prompts/work.md`, plus the authoring rules in `AGENTS.md`
and `tasks/task-template.md`.

## What to build

- **A file-carried gate before the worktree exists.** In
  `start_agent()`, immediately after `_validate` and *before*
  `_claim_for_launch` and any git call, read the task's
  `## Open questions` section and refuse the launch when it has content.
  Nothing is created, so nothing needs cleaning up, and the refusal costs
  no tokens at all.
- **One definition of empty, written down once.** The section counts as
  settled when it is absent, or contains only whitespace, HTML comments,
  or a `None`-style line (`None.`, `- None.`). Anything else is an open
  question. `taskfiles.read_task()` already returns the full `body` and
  is the only module that touches task files, so the parse belongs there
  — expose it as a field rather than re-reading the file in `agents.py`.
- **A refusal that ends the loop instead of starting one.** The error
  names the questions it found, so the fix is obvious and immediate:
  settle them in the card, or move the answered one into the body. A
  refusal that just says "this task has open questions" makes the person
  go hunting for what the board already read.
- **The card does not move.** Unlike a `NOT READY` return, nothing has
  happened yet — no worktree, no branch, no claim. The card stays exactly
  where the user put it.
- **Keep the prompt check too.** Two layers on purpose, the way the
  `**PR:**` line and the actor-only rule both guard PR creation: the gate
  prevents the launch, and the agent's own judgement still catches a
  question phrased somewhere other than the section. Align its wording
  while there — a non-empty section is a refusal on its own terms, not
  something the agent re-adjudicates.
- **Fix the authoring half.** A question you have already answered is not
  an open question: the decision belongs in the body and the alternative
  in Notes. Say so in `tasks/task-template.md`'s guidance for the section
  and in `AGENTS.md` where the section is described. Note that the
  template ships `once` (per `manager/core/release-manifest`), so the
  wording change reaches fresh installs only — which is fine, and worth
  knowing rather than discovering.

**Out of scope** — tempting neighbours left alone:

- Review, relevance and act-PR agents. They read a card that may well
  have open questions and are not doing the work; only work launches
  gate.
- Any change to the `NOT READY` return path — the marker, the walk-back,
  the worktree deletion — which works.
- Showing the state on the card before the click. A card that will
  refuse could say so, and that is the better ergonomics, but it is a
  render change riding on this card's parse. Its own card once this
  lands.
- An override flag to launch anyway. The way to work a card with open
  questions is to answer them.

## Acceptance

- [ ] Given an `in-progress/` card whose `## Open questions` section has
      real content, when **▸ start work** fires, then the launch is
      refused with a message naming the questions — and no worktree,
      branch, claim or agent process is created.
- [ ] Given the same card with the section emptied to `- None.` (or
      deleted), when **▸ start work** fires, then the launch proceeds
      exactly as it does today.
- [ ] Given a card whose section contains only the template's HTML
      comment, when it launches, then it is treated as settled — the
      commented guidance is not an open question.
- [ ] **▸ take over** is gated identically: a takeover of a card with
      open questions refuses for the same reason and names the same
      questions.
- [ ] A relaunch on a card that already has a worktree is gated too — the
      questions matter whether or not work started earlier.
- [ ] Review, PR-review, act-on-PR and **◔ still true?** launches are
      unaffected on the same card.
- [ ] Edge case: a card with no `## Open questions` heading at all
      launches normally — absence is not ambiguity.
- [ ] `prompts/work.md` still instructs the agent to refuse, and the
      existing `NOT READY:` path still works when it does.

## Notes

The board's whole design puts gates in files rather than in intentions —
the directory is the status, the `**PR:**` line stops a second PR, an
existing worktree stops a second launch. Readiness was the one gate left
to a prompt, and prompts persuade where guards refuse.

Immediate consequence worth handling by hand, separately from this card:
**#32** is in progress with its question still open. Either settle the
deploy question in the card or walk it back — whatever this card
eventually enforces, that run should not be the thing that decides it.

**Risks** — the gate is strict by design, and a card left with a stale
"None?" note will now refuse to launch. That is the intended cost, but the
refusal message is what makes it a two-second fix instead of a mystery;
spend the care there.
