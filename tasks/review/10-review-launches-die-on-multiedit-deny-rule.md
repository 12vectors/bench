# 10 — Review launches die at startup: MultiEdit deny rule names a tool that no longer exists

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/23
**Assignee:** istos
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


---

## Work report — 2026-07-30 14:34 (Cleo)

y** instead of checking membership. A new `_flags()` helper returns argv with the generated `--settings` JSON elided (`"<settings>"`), so `review` asserts exactly `-p / --settings / --permission-mode default / --disallowedTools Edit Write NotebookEdit`, and `work` / `act-pr` assert their exact `acceptEdits` lists. Any deny name added, removed or renamed now shows up as a diff a reviewer has to look at, which is what a membership check could never do. The helper also drops a leaked `AGENT_MODEL` from the inherited environment (the same idiom `tests/test_agent_model.py` already uses), because a configured model would otherwise inject `--model` into an argv that is now compared whole.
- `manager/core/adapters/claude/run` — a comment on the review branch recording that this list spells "cannot edit files" in the vendor's own tool names, that the roster moves, that an unknown name is refused at startup so the launch dies before the agent speaks, and that the names must be re-checked against the installed CLI rather than from memory.

**How it was verified**

Beyond the green suite, I confirmed the new assertion actually bites: I temporarily put `MultiEdit` back into the deny list, ran `tests/test_adapter_permissions`, and got the intended failure (a list diff pointing at the extra `MultiEdit`), then reverted it. That negative check is not committed — only the two files above changed.

On the "verify against the installed CLI, not memory" instruction: this headless run's Bash permissions do not allow invoking the `claude` binary, so I could not probe the CLI with a shell command. I verified it from the running process instead — the tool roster available to this very session contains `Edit`, `Write` and `NotebookEdit`, and contains no `MultiEdit`. That is first-hand evidence from the installed CLI, so the three remaining names are the current spelling and the deny intent survives intact.

**For the reviewer — things to act on**

- Confirm you are happy that the exact-argv assertions are the right level of strictness: they will now fail on *any* future flag addition to `run` (a new `--foo` in the mutating branch, for instance), which is deliberate but does make the test a tripwire on unrelated launch changes.
- One related fossil is left in place on purpose: `manager/core/adapters/claude/emit.py:26` still lists `MultiEdit` in `EDIT_TOOLS`. That set only classifies incoming hook events for display, so a retired name there is inert — and it keeps classifying correctly if an older session's events are ever replayed. Say the word if you would rather it were trimmed.

**Acceptance boxes** — the two launch-path boxes (◔ review PR and ◔ still true? reaching a verdict) cannot be ticked from inside a headless worktree run, since they need a live board click and API weather; the flag parsing they died on is what this branch pins down. The remaining two boxes — the deny intent surviving with corrected names, and test coverage of the review-mode flag list — are met.
