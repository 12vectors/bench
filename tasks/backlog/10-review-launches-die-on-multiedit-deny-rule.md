# 10 — Review launches die at startup: MultiEdit deny rule names a tool that no longer exists

**Status:** Backlog
**Priority:** High — every ◔ review PR / ◔ still true? launch fails before the agent starts
**Type:** Bug

Read-only agent launches pass `--disallowedTools Edit Write MultiEdit
NotebookEdit`, and current Claude Code has no `MultiEdit` tool (its
capability merged into `Edit`). The CLI refuses the unknown deny rule
outright — "Permission deny rule \"MultiEdit\" matches no known tool" —
so the launch dies in under a second with "Execution error".

## Context

- `manager/core/adapters/claude/run` (review branch): the
  `--disallowedTools Edit Write MultiEdit NotebookEdit` list is an
  extraction-era fossil; the tool roster it names has moved on.
- Observed: the first ◔ review PR fired at card 02's PR
  (`local/state/agent/logs/review-pr-02-…-215315.log`, 91 bytes, the
  two lines above). Same code path serves the relevance agent
  (◔ still true?), so both are broken.
- Affected areas: `manager/core/adapters/claude/run` only; the tests
  stub the binary, so add the assertion there too
  (`tests/test_adapter_permissions.py` exercises `run` end-to-end).

## What to build

- Drop `MultiEdit` from the deny list. Consider whether the remaining
  names (`Edit`, `Write`, `NotebookEdit`) are still the right, current
  spelling of "cannot edit files" — verify against the installed CLI
  rather than memory, and leave a comment noting the list tracks a
  moving vendor surface.
- A test that catches the next fossil: the stub-binary test should fail
  if `run` passes a deny rule the current tool roster rejects — at
  minimum, assert the exact flag list so a reviewer sees changes to it.

## Acceptance

- [ ] ◔ review PR on a card with a PR launches and runs to a verdict
      (API weather permitting) instead of dying at flag parsing.
- [ ] ◔ still true? launches likewise.
- [ ] Review agents still cannot edit files — the deny intent survives
      with the corrected names.
- [ ] Test suite covers the review-mode flag list.

## Open questions

- None.

## Notes

Found during the first live review-PR attempt (card 02). Sibling of the
fossils in 02/03: things that were true in the origin project's era,
frozen at extraction, discovered one live click at a time — which is
exactly what this dogfooding is for.
