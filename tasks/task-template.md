<!--
Copy this file into backlog/ as NN-short-kebab-title.md — numbers are
allocated in creation order and stay with the task for life. The board never
lists this template (it only reads the stage directories). The **PR:** line
is added by the board itself when the task reaches review/ with a branch.

Anything below marked *optional* is deletable, and deleting beats leaving
it hollow — an empty boilerplate section reads as thinking that never
happened. Board process (review, PR, CI, merge) and the project-wide
definition of done stay off the card: the board does the former
mechanically and the repo AGENTS.md owns the latter.

This template lives in tasks/, which updates never touch — improvements to
it ship only with fresh installs, so local edits are yours to keep.
-->

# NN — Imperative title: what changes when this is done

**Status:** Backlog
**Priority:** Medium — one clause on why it sits at this level
**Type:** Feature
**Depends on:** 03, 05 — task numbers or external preconditions that must
land first, for whoever sequences the board; delete when nothing blocks this

One paragraph for someone — human or agent — who has the codebase but not
the conversation: what this task changes, and why it is worth doing.

## Context

What exists today and why it falls short. Point at real places rather than
describing from memory: packages and modules (`packages/domain/...`), prior
tasks (`../done/...`), plan files (`../../plans/...`) and reference
documents (`../../reference/...`) — a link outlives a summary.

**Affected areas:** the modules or layers this touches, one line in the
repo AGENTS.md's module-map vocabulary — telling reviewers where to look
and agents where to stop. Optional: delete when the title already says it.

## What to build

The work itself, concrete enough to start on. Name the layers things belong
in — the repo AGENTS.md's dependency rules decide where code goes, not
convenience.

- First piece
- Second piece

**Out of scope** — the adjacent changes this task deliberately does not
make. This is what bounds the work agent's brief; scope creep is the
classic headless failure. Optional, but cheap insurance on any task with
tempting neighbours — delete rather than leave empty.

- Not this, even though it is nearby

## Acceptance

Observable outcomes, not implementation steps — review agents judge the
diff against exactly this list. The repo's definition of done (the
project's configured checks pass, new behaviour covered) applies on top;
don't restate it. Given/When/Then phrasing is welcome where it sharpens a criterion, and
edge cases belong here too — boundaries, empty inputs, failure paths.

- [ ] Something a reviewer can check without reading the diff
- [ ] Given <a state>, when <the action>, then <the observable result>
- [ ] Edge case: the boundary that would embarrass this feature if missed

## Open questions

Decisions only the task's author can settle. This section is load-bearing:
an agent told to start work while anything real sits here will refuse with
`NOT READY` and send the card back — that is the point. Empty it (or delete
it) when the task is ready to action.

- None.

## Notes

Freeform: research findings, links, decisions taken along the way. The
board's relevance checks and PR reviews append their reports below this
line as the task moves.

**Risks** — known hazards and blockers: what could sink the approach, what
the change might break, what must hold for it to work. For the author
weighing the task and the reviewer double-checking it. Optional: delete
when there is nothing real to name.

- None worth naming yet.
