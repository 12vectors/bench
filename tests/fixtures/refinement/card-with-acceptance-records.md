# 90 — A card whose acceptance is tracked

**Status:** Review
**Priority:** Medium
**Type:** Feature
**PR:** https://example.com/pr/90

## Context

A fixture for the shared persistence contract between card refinement
(task 65) and structured acceptance (task 81). Criterion ids are written on
the criterion's own line; acceptance records live in history and are
written by the board only.

## Acceptance

- [x] Comments are saved with attribution <!-- ac:saved -->
- [ ] Saving a comment runs nothing <!-- ac:no-run -->
- [ ] A criterion nobody has identified yet

---

## Work report — 2026-11-08 12:00 (Wren)

The implementation report, kept as history.

## Acceptance record

<!-- bench:acceptance {"by": "ron", "criterion": "saved", "state": "accepted", "v": 1} -->
<!-- bench:acceptance {"by": "ron", "criterion": "no-run", "state": "pending", "v": 1} -->

## Discussion

<!-- bench:discussion v1 — written by the board. Entries are history: the brief above is the requirement. -->

### Comment c-0a1b2c3d4e — ron · 2026-11-08 15:40 UTC
<!-- bench:entry {"at": "2026-11-08T15:40:00Z", "author": "ron", "id": "c-0a1b2c3d4e", "kind": "comment", "v": 1} -->

> Also cover switching external root A to B while preserving both roots' data.

### Applied p-5f6a7b8c9d — ron · 2026-11-08 16:02 UTC
<!-- bench:entry {"at": "2026-11-08T16:02:00Z", "author": "ron", "briefAfter": "after", "briefBefore": "before", "comment": "c-0a1b2c3d4e", "id": "d-1a2b3c4d5e", "kind": "decision", "outcome": "applied", "proposal": "p-5f6a7b8c9d", "sections": ["Acceptance"], "v": 1} -->

> Added an acceptance criterion for switching roots.
