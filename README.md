# Bench

A live kanban for coding-agent work: task files in stage directories are
the only source of truth; a stdlib-only board narrates everything that
happens to them — agents working in git worktrees, PRs opening on review,
CI and Copilot state on the cards, drives of the app from a task's own
branch, and an archive that is never a delete. Turn on team mode
(`BOARD_SYNC=1`) and the truth is `origin/main`: moves commit and push
themselves, every board pulls on a beat, and the person who claimed a
card first keeps it.

## Install into a repo

```bash
mkdir .task-manager && curl -L \
  https://github.com/12vectors/bench/releases/latest/download/bench.tar.gz \
  | tar -xz -C .task-manager
./.task-manager/start.sh        # wires the project (idempotent) and serves
```

No token, no clone: releases are curated artifacts that never contained
bench's own cards or settings, so the board starts empty by construction.

Commit `.task-manager/` into the host repo — core is vendored on purpose,
so clones work offline and updates show up in the host's own diffs.

The workflow brief ships as `.task-manager/AGENTS.md` — the cross-vendor
name coding agents read natively — with `CLAUDE.md` beside it as a one-line
compatibility pointer. Both live inside `.task-manager/`, so a host repo's
own root `AGENTS.md` is never touched.

## Update

```bash
./.task-manager/update.sh              # latest release
BENCH_REF=v2 ./.task-manager/update.sh # an exact release tag
```

The artifact is stamped with the repo it was built from, so updating
needs no configuration; `BENCH_SOURCE=<owner/repo>` in
`manager/local/.env` overrides the stamp. Updates replace
`manager/core/` and the top-level scripts wholesale and touch nothing
else — tasks, plans, reference, and everything under `manager/local/`
(your driver, commands, prompt overrides, settings, state) survive every
update. Then `python3 .task-manager/install.py` and restart the board.
If the source repo has no published release yet, `update.sh` says so and
changes nothing.

## Working on bench itself

```bash
git clone git@github.com:12vectors/bench.git && cd bench && ./start.sh
```

A clone carries bench's own cards and local/ content — that is dev mode,
not an install. (Installing from a clone anyway works: `install.py`
clears the shipped cards on its first boot in a host repo.) Releases are
built by `./release.sh` from the manifest at
`manager/core/release-manifest`: tag = `v<VERSION>`, one stable asset
name (`bench.tar.gz`), contents at the tarball root — the two things the
install one-liner above depends on.

## The three-layer law

Core knows about tasks, worktrees, PRs and events. It knows nothing about
any particular app (drivers do: `local/driver/start`), agent vendor
(adapters do: `core/adapters/`), or project (`local/` does). Full docs in
AGENTS.md; the adapter contract in `manager/core/adapters/README.md`.

## License

[MIT](LICENSE).
