# 79 — Make the Configuration check readable

**Status:** To Do
**Priority:** High
**Type:** Feature

## Context

The Configuration check is long and its first screen does not say what to
do next.

## What to build

- Lead with one specific result and one scope sentence.
- Fold rows that passed apart from rows that were not tested.

## Acceptance

- [ ] The headline names a specific result <!-- ac:headline -->
- [ ] Untested rows never read as passed <!-- ac:untested -->

## Acceptance record

<!-- bench:acceptance-record v1 — written by the board. One decision per line; the newest about a criterion stands. -->

<!-- bench:acceptance {"actor": "istos", "actorSource": "git", "at": "2026-11-09T10:00:00Z", "criterion": "headline", "id": "r-0000000001", "rev": "aaaaaaaaaaaa", "source": "tracked", "state": "pending", "text": "The headline names a specific result", "v": 1} -->

---

## Relevance review — 2026-11-10 14:22 (Hazel)

RELEVANCE REVIEW: Partly done

**What I checked.** `manager/core/doctor.py` already writes
`summary.headline` and `summary.scope`, so the first bullet of *What to
build* is in place.

- The headline is specific: *No configuration issues found*.
- Folding is **not** done: passed and untested rows still share one list.

| Area | State |
| --- | --- |
| Headline | done |
| Folding | open |

Recommendation: update the task — drop the headline bullet, keep the
folding work, and add a criterion for the 390px layout.

<script>alert('x')</script> [a link](javascript:alert(1))
