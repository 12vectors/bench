# manager/core/seed — tool-owned starter material

What is here belongs to the tool, not to any board. It ships inside
`manager/core/`, so every update replaces it wholesale, and nothing reads
it as work state: the board never lists it and never writes to it.

`install.py` uses it in exactly one way. When the resolved work root has
no `tasks/task-template.md`, the installer copies `task-template.md` from
here into place. That copy is yours from then on — an existing template
at the work root, customised or not, is never overwritten, and an update
changes only this seed, never the copy.

The active work directories — `tasks/` with its stages and `archive/`,
`plans/` and `reference/` — are not shipped at all. The installer creates
them at the work root the layout resolves to: the install itself when
vendored, `BOARD_WORK_ROOT` when that is set. See "Where the work state
is created" in the root `AGENTS.md`.
