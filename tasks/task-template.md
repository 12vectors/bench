<!--
Copy this file into backlog/ as NN-short-kebab-title.md — numbers are
allocated in creation order and stay with the task for life. The board never
lists this template (it only reads the stage directories). The **PR:** line
is added by the board itself when the task reaches review/ with a branch.
-->

# NN — Imperative title: what changes when this is done

**Status:** Backlog
**Priority:** Medium — one clause on why it sits at this level
**Type:** Feature

One paragraph for someone — human or agent — who has the codebase but not
the conversation: what this task changes, and why it is worth doing.

## Context

What exists today and why it falls short. Point at real places rather than
describing from memory: packages and modules (`packages/domain/...`), prior
tasks (`../done/...`), reference documents (`../../reference/...`).

## What to build

The work itself, concrete enough to start on. Name the layers things belong
in — the repo CLAUDE.md's dependency rules decide where code goes, not
convenience.

- First piece
- Second piece

## Acceptance

Observable outcomes, not implementation steps. The repo's definition of done
(tests pass, `lint-imports` clean, new behaviour covered) applies on top.

- [ ] Something a reviewer can check without reading the diff
- [ ] Another one

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
