# 204 — Show the time zone beside every timestamp in the Activity view

**Status:** Review
**Assignee:** istos
**Priority:** Medium
**Type:** Bug

Activity shows local times without saying which zone they are in, so two teammates in different cities read the same event at different hours. Name the zone beside each timestamp, and keep the underlying UTC value one hover away.

## Context

Reports filed by runs carry UTC stamps; comments carry the reader's local time. Both appear in one list with no zone, and the order looks wrong across midnight.

## What to build

- Every Activity timestamp names its zone (the reader's local zone abbreviation).
- Hovering or focusing a timestamp shows the UTC value it was recorded with.
- Sorting uses the recorded instant, never the rendered string.

## Acceptance

- [ ] Every timestamp in Activity names its zone. <!-- ac:zone -->
- [ ] The UTC value is reachable by keyboard as well as by hover. <!-- ac:keyboard -->
- [ ] Items stay in instant order across midnight. <!-- ac:order -->

---

## Work report — 2026-11-18 10:41 (Wren)

Committed and tested on task/204: Activity timestamps now name the reader's zone and sort by the recorded instant. The UTC value is in the element's title only, so it is not yet reachable by keyboard.

---

## PR review at 7d1e0aa94c21 — 2026-11-18 14:05 (Olive)

PR REVIEW: REQUEST CHANGES

The zone is shown and the order holds across midnight, but the UTC value is only in a `title` attribute, which a keyboard user cannot reach. Make it focusable or show it on focus.
