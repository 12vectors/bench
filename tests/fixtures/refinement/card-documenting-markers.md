# 91 — A card that documents Bench's storage syntax

**Status:** To Do
**Priority:** High
**Type:** Feature

## Context

Reproduces card 81's shape (task 82): a brief that documents the storage
format it will read. An acceptance record is one line under
`## Acceptance record`, for example
`<!-- bench:acceptance {"criterion": "no-run", "state": "accepted", "v": 1, …} -->`,
and a discussion entry is introduced by `<!-- bench:entry {…} -->`.

A whole record, as it sits in a card:

```markdown
<!-- bench:acceptance {"criterion": "saved", "state": "accepted", "v": 1} -->
<!-- bench:entry {"id": "c-0000000000", "kind": "comment", "v": 1} -->
```

## Acceptance

- [ ] Records are read from history only <!-- ac:history-only -->
- [ ] Examples in the brief stay examples <!-- ac:examples -->

---

## Work report — 2026-11-09 09:00 (Wren)

Kept as history.

## Acceptance record

<!-- bench:acceptance {"by": "ron", "criterion": "history-only", "state": "accepted", "v": 1} -->

## Discussion

<!-- bench:discussion v1 — written by the board. Entries are history: the brief above is the requirement. -->

### Comment c-1a1a1a1a1a — ron · 2026-11-09 09:10 UTC
<!-- bench:entry {"at": "2026-11-09T09:10:00Z", "author": "ron", "id": "c-1a1a1a1a1a", "kind": "comment", "v": 1} -->

> Apply the latest relevance review's recommendations.
