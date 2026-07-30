# 03 — Focus checks come from the project, not from core

**Status:** Backlog
**Priority:** Medium — cosmetic on bench's own board, but every adopting project sees a stranger's definition of done
**Type:** Feature

The Focus view's CHECKS panel shows three fixed rows — `pytest`,
`lint-imports`, `frontend` — which are the definition of done of the
project bench was extracted from, hardcoded into core. In any other
project (including bench itself: stdlib Python, no vitest, no vue-tsc)
the panel shows rows that can never run. Per the three-layer law, what
counts as a check is project knowledge and belongs in `local/`.

## Context

Checks are observational, not executed: the board never runs anything.
The claude adapter's hooks classify each Bash command an agent runs, and
Focus shows the last matching event per row. The hardcoding sits in two
load-bearing places and two soft ones:

- `manager/core/adapters/claude/emit.py:92-95` — classification: `pytest`
  → kind `test`; `lint-imports` / `type-check|vue-tsc|npm (run )?test|vitest`
  → kind `check`. Pass/fail for tests is parsed pytest-style
  (`N passed` / `N failed`).
- `manager/core/board.html:1488-1500` — the Focus panel's three fixed
  rows, each with its own matching regex (`lint-imports`,
  `type-check|vitest|npm`) duplicating the adapter's patterns.
- `tasks/task-template.md:35` and `CLAUDE.md:190` — both recite
  "pytest / lint-imports / frontend" as *the* definition of done in
  core-owned, distributed text.

Precedents for where project knowledge lives: prompts resolve
`local/prompts/` over `core/prompts/` by filename; settings resolve env >
`local/.env` > defaults. Checks should follow the same shape.

## What to build

First a short investigation (this is half discovery): confirm the event
payloads give enough to match and judge arbitrary commands — notably
whether pass/fail can rest on something sturdier than per-tool output
regexes (exit status in the hook response, if present, beats parsing
"N passed").

Then, informed by that:

- A project-owned checks definition — proposed: a small file resolved
  like prompts (core ships a default, same filename in `local/` wins),
  each entry a label plus a command-matching regex, e.g.
  `pytest: \bpytest\b`. Core reads it; the adapter reads the same file
  for classification so the two never drift again.
- `emit.py`: classify against the resolved definitions instead of inline
  literals; keep a generic fallback for pass/fail judgment.
- Focus (`board.html`): render one row per defined check, served via the
  existing state API — no fixed rows, no duplicated regexes.
- Sand off the soft leaks: reword `task-template.md` and `CLAUDE.md` so
  the definition of done is described generically ("the project's
  configured checks"), and document the checks file in
  `core/.env.example` or the checks default itself.
- Bench's own `local/` gets a minimal real definition (whatever bench
  actually has — even just a `python -m compileall`-style smoke row)
  so the self-hosted Focus view shows checks that can actually run.

## Acceptance

- [ ] A project defining its own checks in `local/` sees exactly those
      rows in Focus, matched and judged from live agent events.
- [ ] A project defining nothing sees the shipped default rows —
      behaviour today survives as the default, not as the only option.
- [ ] Nothing in `manager/core/` names pytest, lint-imports, vue-tsc or
      vitest outside the shipped default checks definition.
- [ ] Adapter classification and Focus rendering read the same
      definition — one place to edit, verified by changing a label and
      seeing it flow to both.

## Open questions

- None.

## Notes

Found while running the self-hosted board: Focus showed `pytest /
lint-imports / frontend` as bench's checks, all "not run" — they are the
origin project's, frozen at extraction time (the same fossil layer as
the `.task-manager/` path assumption in
`../backlog/02-start-cleanly-when-bench-is-the-project.md`).
