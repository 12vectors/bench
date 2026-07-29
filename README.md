# Bench

A live kanban for coding-agent work: task files in stage directories are
the only source of truth; a stdlib-only board narrates everything that
happens to them — agents working in git worktrees, PRs opening on review,
CI and Copilot state on the cards, drives of the app from a task's own
branch, and an archive that is never a delete.

## Install into a repo

```bash
git clone <this repo> .task-manager && rm -rf .task-manager/.git
./.task-manager/start.sh        # wires the project (idempotent) and serves
```

Commit `.task-manager/` into the host repo — core is vendored on purpose,
so clones work offline and updates show up in the host's own diffs.

## Update

```bash
# in manager/local/.env:  BENCH_SOURCE=<this repo's git url>
./.task-manager/update.sh              # latest
BENCH_REF=v1 ./.task-manager/update.sh # a specific release tag
```

Updates replace `manager/core/` and the top-level scripts wholesale and
touch nothing else — tasks, plans, reference, and everything under
`manager/local/` (your driver, commands, prompt overrides, settings,
state) survive every update. Then `python3 .task-manager/install.py` and
restart the board.

## The three-layer law

Core knows about tasks, worktrees, PRs and events. It knows nothing about
any particular app (drivers do: `local/driver/start`), agent vendor
(adapters do: `core/adapters/`), or project (`local/` does). Full docs in
CLAUDE.md; the adapter contract in `manager/core/adapters/README.md`.
