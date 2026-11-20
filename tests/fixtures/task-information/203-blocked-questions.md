# 203 — Retire the legacy phase runner once every phase is scoped

**Status:** To Do
**Priority:** High — two schedulers are a standing source of confusion
**Type:** Refactor
**Depends on:** 202, 999

Remove the legacy phase runner after every phase on supported boards is run by its own phase scope, so one scheduler decides what a phase does next.

## Problem

A phase can be run by the legacy beat or by its scope, and the board has to explain which one owns it on every surface. Keeping both doubles the tests and the documentation.

## Scope

1. Refuse ▸ run phase on a board where every phase is adopted, with the remedy.
2. Remove the runner's beat and its log-reading code.
3. Rewrite the Phases documentation for one scheduler.

## Open questions

- Do we keep reading old `## Phase log` lines for history, or drop them?
- Which release is the last that runs an unscoped phase?

## Acceptance

- [ ] No phase can be scheduled by two owners.
- [ ] An existing phase log still reads as history.
