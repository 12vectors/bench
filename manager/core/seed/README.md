# manager/core/seed — tool-owned starter material

What is here belongs to the tool, not to any board. It ships inside
`manager/core/`, so every update replaces it wholesale, and nothing reads
it as work state: the board never lists it and never writes to it.

`install.py` uses it in two ways, both the same rule: copy a file into
place only where none exists.

- When the resolved work root has no `tasks/task-template.md`, the
  installer copies `task-template.md` from here into place.
- When the install's `manager/local/` has no `AGENTS.md` or no
  `CLAUDE.md`, it copies that one file from `local/` here (task 78). The
  starter names no test framework; a project's definition of done is its
  own to write.

Each copy is yours from then on. An existing file, customised or not, is
never overwritten, and an update changes only this seed, never the copy.

The active work directories — `tasks/` with its stages and `archive/`,
`plans/` and `reference/` — are not shipped at all. The installer creates
them at the work root the layout resolves to: the install itself when
vendored, `BOARD_WORK_ROOT` when that is set. See "Where the work state
is created" in the root `AGENTS.md`.
