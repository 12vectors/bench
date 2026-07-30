# 14 — Work agents branch from the latest origin/main, not from whatever HEAD is

**Status:** Archived
**PR:** https://github.com/12vectors/bench/pull/7
**Priority:** Medium — invisible today because one person pushes from one machine; first teammate or GitHub-side merge makes it bite
**Type:** Feature

A fresh work launch creates the task branch from the main checkout's
local `HEAD`. Nothing fetches first, so the agent builds on the board
machine's possibly-stale view of the world: anything merged on GitHub,
pushed by a teammate, or landed by act-pr from elsewhere is missing,
and the resulting PR is born conflicted or re-solves solved problems.
Starting work should mean starting from the newest main that exists.

## Context

- `manager/core/agents.py:150-176` — the worktree setup.
  Fresh branch: `base = rev-parse HEAD`, `worktree add -b` from it
  (`:175-176`). No `fetch` anywhere in core.
- The board already treats origin as the source of truth on the *other*
  side of the flow: PR opening refuses when local main is ahead of the
  remote, and merge & clean-up pushes after merging. Launching is the
  one leg that never looks at origin.
- Continuing paths (existing worktree `:158-168`, existing branch
  `:171-173`) resume prior work — their base is history, not a choice,
  so they are unaffected; freshness for an in-flight branch is act-pr's
  problem, prompted by reviews, not the launcher's.
- Bench must keep working remoteless: `git remote` may list nothing,
  and the network may be down. Today's behaviour is the only correct
  one in that world.

**Affected areas:** `agents.py` (worktree setup) only; the ticker line
it emits. No adapter or prompt involvement.

## What to build

- Before a *fresh* `worktree add -b`: if a remote exists, `git fetch
  origin main` with a short timeout; on success, base the new branch on
  `origin/main` instead of `HEAD`. On no-remote, fetch failure, or
  timeout: fall back to `HEAD` exactly as today — launching must never
  be blocked by network weather.
- Never touch the main checkout itself: no fast-forwarding, no pulling
  — the user may be sitting in it with uncommitted work. The fetched
  ref is used only as the branch point. (Local main catches up
  naturally at merge & clean-up time, which already merges and pushes.)
- Narrate honestly in the ticker: when the branch point is origin/main
  and local main is behind it, say so ("branched from origin/main,
  N ahead of this checkout"); when the fetch failed and HEAD was used,
  say that too. Silent freshness is as bad as silent staleness.

**Out of scope** — tempting neighbours left alone:

- Rebasing or updating existing task branches (act-pr territory,
  review-driven).
- Fast-forwarding the user's main checkout.
- Any remote other than `origin`, or configurable base branches — main
  is the flow's spine everywhere else in core.

## Acceptance

- [ ] Given origin/main ahead of local main, when ▸ start work fires,
      then the new worktree's merge-base with origin/main is
      origin/main's tip, and the ticker names the branch point.
- [ ] Given no remote configured, when ▸ start work fires, then the
      launch behaves byte-identically to today (base = HEAD, no fetch
      attempted, nothing new in the ticker).
- [ ] Edge case — network down with a remote configured: launch
      proceeds from HEAD after the timeout, ticker says the fetch was
      skipped, and total launch delay stays within the timeout bound.
- [ ] Relaunch onto an existing worktree/branch is unchanged.

## Open questions

- None.

## Notes

Requested by the owner (2026-07-30) after watching origin advance
independently of the checkout during the first multi-lane day — board
merges pushing while other work was in flight made "which main did this
agent start from?" a live question for the first time.

**Risks**

- A fetch on every launch adds seconds and a network dependency to the
  hottest path on the board — the timeout-and-fall-back behaviour is
  the load-bearing part, not the fetch.
- Basing on origin/main while local main is behind means the worktree
  contains commits the main checkout lacks; anything comparing the two
  (diff stats in Focus read from the worktree — verify) must not
  assume worktree ⊆ local main.


---

## Work report — 2026-07-30 07:27 (Juno)

ing report.

---

The work is committed on this branch (two commits, `4a91e5a` and `92b0c54`) and verified: the full suite — 56 tests, including 7 new ones for this behaviour — passes with `python3 -m unittest discover -s tests`.

**What changed.** Fresh work launches now branch from the newest main that exists instead of the board checkout's HEAD. A new helper `_fresh_branch_point()` in `manager/core/agents.py` checks for an `origin` remote, fetches `origin/main` under a timeout, and hands `start_agent` the branch point plus a ticker note; the fresh-branch path then runs `git worktree add --no-track -b` from `origin/main` (no-track so the task branch doesn't adopt origin/main as its upstream, matching today's untracked branches). No remote, a failed fetch, a missing `origin/main` ref, or a timeout all fall back to branching from HEAD exactly as before — the launch is never blocked. The main checkout is never touched; the fetched ref is only the branch point. The timeout is a new setting, `BOARD_FETCH_TIMEOUT` (default 10s), added to `manager/core/config.py` and documented in `manager/core/.env.example`. The launch line in the ticker now narrates when it matters: "branched from origin/main, N ahead of this checkout" when local main is behind, and "fetch of origin/main timed out/failed; branched from local HEAD" when the network lost. An in-sync origin and the no-remote case stay quiet, as today. The canonical brief in `CLAUDE.md` was updated to stop saying fresh worktrees branch "from current HEAD".

**How it was verified.** `tests/test_fresh_branch_point.py` exercises the helper against real git repositories: origin ahead by two commits yields `origin/main` with the "2 ahead" note and a worktree whose merge-base with origin/main is origin's tip; no remote and a non-origin remote return HEAD silently and fast; an unreachable origin falls back with the failure note; and a genuinely hanging fetch (simulated with git's `ext::` transport running `sleep`) returns within the timeout bound with the timed-out note. The continuing-worktree and existing-branch paths were not modified, and the pre-existing suites (empty-branch guard, adapter permissions, self-hosting) still pass.

For the reviewer:

- Read `manager/core/agents.py` first — `_fresh_branch_point()` and the fresh-branch arm of `start_agent` are the whole change.
- Run `python3 -m unittest discover -s tests` to confirm (the hanging-fetch test adds ~1s).

One interaction worth knowing, left as-is deliberately: a *relaunch* onto an existing worktree still computes its diff base as `merge-base(main, branch)`. If the branch was born from an origin/main that local main hadn't caught up to, a relaunch's Focus diff stats will include those origin commits until local main catches up (which merge & clean-up already does). The acceptance criteria pin relaunch behaviour as unchanged, so I did not touch it — it is a display-accuracy nuance, not a correctness issue, but a fair candidate for a follow-up card.


---

## PR update — 2026-07-30 07:37 (Basil)

ADDRESSED: The "N ahead" ticker count now compares origin/main against HEAD instead of the local main branch, so it stays accurate when the board runs from another branch.

The fix is committed as `0111a9f` and pushed, so the PR is updated; the full suite — 57 tests, including one new one for this case — passes with `python3 -m unittest discover -s tests`.

- **Copilot's line comment on `manager/core/agents.py` (the only review point):** the note "branched from origin/main, N ahead of this checkout" computed N as `rev-list --count main..origin/main`, which is only correct when the checkout is actually on `main` — from another branch or a detached HEAD the number could be wrong or misleading. Fixed by counting `HEAD..origin/main` instead. HEAD is what the fallback base would have been, so the count is now literally "what launching from this checkout would have missed", matching the note's wording regardless of which branch the board runs from. A new test, `test_ahead_count_is_relative_to_the_checkout_not_local_main` in `tests/test_fresh_branch_point.py`, pins the behaviour: a checkout already holding origin/main's tip on a side branch (with local main behind) now correctly gets no "ahead" narration, where the old count would have claimed "1 ahead".

The review was a COMMENTED verdict (no approve/request-changes), and Copilot's PR overview raised nothing beyond that one line comment, so there is nothing else outstanding from this round.


---

## PR update — 2026-07-30 07:39 (Juno)

ADDRESSED: Nothing new to change — the sole review point (Copilot's "ahead" count) was already fixed in pushed commit 0111a9f; verified in code and by the 57-test suite passing.

- **Copilot's line comment on `manager/core/agents.py` (count "ahead" relative to the checkout, not local `main`):** already addressed before this round by commit `0111a9f`, which is on the remote branch, so the PR is current. I re-verified rather than assuming: `_fresh_branch_point()` now counts `HEAD..origin/main`, which is precisely "commits since the merge-base of the checkout and origin/main" — the semantics Copilot suggested — and the regression test `test_ahead_count_is_relative_to_the_checkout_not_local_main` (`tests/test_fresh_branch_point.py:101`) covers the misleading case (origin/main's tip already on a side branch, local main behind → no "ahead" note). Full suite: 57 tests, all passing.
- **No other feedback exists:** the review verdict was COMMENTED (no approve/request-changes), the PR overview raised nothing beyond that one line comment, and there are no issue comments or human reviews. Nothing was skipped or disagreed with; there was simply nothing else outstanding, so no new commits were needed.
