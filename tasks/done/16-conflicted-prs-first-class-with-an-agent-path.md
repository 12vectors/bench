# 16 — Surface PR conflicts on the card and give act-on-PR the tools to resolve them

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/12
**Priority:** High — the first real conflict (PR #7, card 14) had no bench path at all; parallel lanes make the next one soon
**Type:** Feature

A conflicted PR is invisible to the board and unactionable through it:
the poller doesn't read mergeable state, the act-pr prompt knows
nothing about stale branches, and the act-pr allowlist would deny
`git fetch`/`git merge` the moment an agent tried. Resolution is
currently entirely by hand. Make conflict a state the card wears, and
mechanical resolution a job ↻ act on PR can do — while teaching it to
refuse the conflicts that are really product decisions.

## Context

- Observed on PR #7 (`task/14-…`): GitHub says CONFLICTING/DIRTY; the
  board said nothing; no action chip helps.
- `manager/core/github.py` — the poller folds reviews + CI into one
  verdict; `mergeable`/`mergeStateStatus` (available via
  `gh pr view --json`) is not fetched, so the card cannot know.
- `manager/core/prompts/act-pr.md` — entirely review-comment-driven;
  no concept of the branch being behind or conflicted with main.
- `manager/core/adapters/claude/hook_settings.py:37-38` — act-pr
  prefixes: add/commit/status/diff/push + gh pr view/diff/api. No
  `git fetch`, no `git merge`: resolution is permission-denied today.
  (The opencode adapter mirrors the same stances — change both.)
- Board merge & clean-up already aborts cleanly on conflict — correct,
  and unchanged by this card; it just stops being the first time
  anyone hears about the problem.
- `../review/14-work-agents-branch-from-latest-origin-main.md` reduces
  how often fresh branches start stale; this card handles the
  conflicts that parallel lanes produce anyway.

**Affected areas:** `github.py` (poll mergeable), `board.html` (chip),
`prompts/act-pr.md`, both adapters' permission stances + their tests.

## What to build

- **See it**: poller fetches mergeable state with the fields it already
  reads; a conflicted PR puts an alarm-coloured `conflicts` chip on the
  card (a destination-style chip in the footer row, per the design
  system) and folds into the card's verdict as changes-needed-by-you,
  not as CI failure.
- **Arm the agent**: act-pr allowlist gains `git fetch` and `git merge`
  (both adapters). Deliberately NOT `git rebase` and no force-push —
  the branch is public; resolution must be additive.
- **Teach the prompt**: act-pr.md gains a conflicts section — if the PR
  conflicts with main: fetch, `git merge origin/main`, resolve honouring
  both sides' intent, run the project's tests until green, put the
  resolution in its own commit whose message names the files and the
  choices, push, and cover the resolution explicitly in the ADDRESSED
  report. If both intents cannot hold simultaneously — main has made
  the branch's premise false — resolve nothing: abort the merge, and
  report that a human must decide, stating the specific collision.
  Guessing at semantic conflicts is the one forbidden move.
- **Keep the human at the merge**: nothing auto-merges (unchanged);
  the resolved PR flows into the existing ◔ review loop, which now also
  judges the resolution commit.

**Out of scope** — tempting neighbours left alone:

- Auto-resolving on the board's own merge & clean-up path (it keeps
  aborting cleanly; the fix is upstream, before you drag to done).
- Rebase workflows, force-push, and history rewriting of any kind.
- Proactively merging main into healthy, non-conflicted branches.

## Acceptance

- [ ] Given a PR GitHub marks CONFLICTING, when the poller next runs,
      then the card wears the conflicts chip and drops any
      approved-green state until resolved.
- [ ] Given a mechanically conflicted PR, when ↻ act on PR runs, then
      the branch gains a merge-resolution commit, tests pass, the push
      updates the PR to mergeable, and the ADDRESSED report names the
      conflicted files and the choices made.
- [ ] Given a semantic conflict (a fixture branch whose premise main
      has removed), when ↻ act on PR runs, then no resolution is
      committed and the report says a human must decide, naming the
      collision.
- [ ] Edge case — permission proof: the act-pr stance test shows
      `git fetch`/`git merge` allowed, `git rebase` and force-push
      absent, in both adapters.
- [ ] Merge & clean-up behaviour on conflict is unchanged.

## Open questions

- None.

## Notes

Prompted by PR #7 (card 14, 2026-07-30): the card about branching from
fresh main was itself the first stale-base conflict — it edits
`agents.py`, which cards 12 and others had just changed. Until this
card lands, conflicted PRs are hand-work in the task's worktree.

**Risks**

- An agent's "faithful to both intents" is a judgment call; the
  guardrails (own commit, tests green, review loop judges it, human
  merges) bound the blast radius, but a plausible-wrong resolution
  surviving lazy review remains the real hazard. The refuse-on-semantic
  rule in the prompt is the load-bearing mitigation — watch its
  false-confidence rate in early use.
- `mergeable` from GitHub is computed lazily and can read UNKNOWN for
  a while after pushes; the chip must tolerate the unknown state
  without flapping between colours.


---

## Work report — 2026-07-30 07:58 (Sage)

ed**

- `manager/core/github.py` — the poller now asks `gh pr view` for `mergeable` alongside the fields it already read. A CONFLICTING PR drops any approved-green verdict as changes-needed-by-you (the CI chip is untouched — a conflict is not a CI failure), and the flip narrates in the ticker both ways. GitHub computes mergeability lazily, so an UNKNOWN reading keeps the previous state instead of flapping the chip. To make this testable without `gh`, the poll logic was extracted into a pure function `_fold(data, prev)`; `_poll_pr` now just fetches, folds, and narrates.
- `manager/core/board.html` — a conflicted PR puts an alarm-coloured `conflicts` chip in the card's footer row, beside CI/copilot/PR, per the design system's destination-chip pattern.
- Both adapters (`manager/core/adapters/claude/hook_settings.py`, `manager/core/adapters/opencode/permission_config.py`) — the act-pr stance gains `git fetch` and `git merge`. One thing worth a reviewer's attention: I went slightly beyond "absent" for force-push, because the existing `git push` allow already covers `git push --force` by prefix. Both adapters now carry explicit deny rules for the force spellings and `git rebase` — deny-beats-allow in the Claude settings, last-match-wins globs in the opencode config. Prefix rules can't catch every exotic flag placement; the prompt and review loop remain the guard there, as the code comments say.
- `manager/core/prompts/act-pr.md` — a conflicts section: fetch, `git merge origin/main`, honour both intents, tests green, the resolution in its own commit naming files and choices, covered explicitly in the report — and the refusal rule: if main has made the branch's premise false, `git merge --abort`, resolve nothing, and name the collision for a human. `manager/core/adapters/README.md` and the root `AGENTS.md` document the widened contract and the new chip.
- Merge & clean-up (`complete_task`) is untouched, per the task's out-of-scope list.

**How it was verified**

- New `tests/test_pr_conflicts.py`: conflict drops green even when approved, conflict ≠ CI failure, UNKNOWN keeps the previous reading both ways (no flapping), first-sight UNKNOWN alarms nobody, resolution lets green return, and the `conflicts` key reaches `public_state()` and the chip markup.
- New stance tests in `tests/test_adapter_permissions.py`: fetch/merge allowed in act-pr only, rebase and `--force` absent from every allowlist, the denies present in both adapters, and the opencode denies ordered after the push allow so they win.
- The live-agent acceptance criteria (a real mechanical resolution, a real semantic refusal) are exercised only by an actual ↻ act on PR run — worth watching on the first conflicted PR this meets.

**To check first as a reviewer**

- The deny-rule decision described above — it's the one place I interpreted the spec rather than transcribed it.
- The `_fold` extraction diff in `manager/core/github.py` (the logic move is mechanical, but it's the load-bearing change).
