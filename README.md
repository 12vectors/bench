# Bench

A live kanban for coding-agent work: task files in stage directories are
the only source of truth; a stdlib-only board narrates everything that
happens to them — agents working in git worktrees, PRs opening on review,
CI and Copilot state on the cards, drives of the app from a task's own
branch, and an archive that is never a delete. Work that is a run of
related cards rather than one is a **phase**: a card that lists its
cards, an integration branch of its own, and members branched from it,
run one at a time and merged back on green — ending in a single PR into
`main` for a human. Turn on team mode
(`BOARD_SYNC=1`) and the truth is `origin/main`: moves commit and push
themselves, every board pulls on a beat, and the person who claimed a
card first keeps it.

**Docs: [bench.12vectors.com](https://bench.12vectors.com)** — guides,
concepts, and a settings reference, every page of it generated from this
repository's own markdown, so what the docs say and what bench does
cannot drift apart.

![The bench board: cards in backlog, to-do, in-progress, review and done, three agents working, each card showing the branch it is on and the command its agent is running.](https://raw.githubusercontent.com/12vectors/bench/main/site/static/board.png)

*bench's own board, running on this repository. Three agents at work,
each on its own branch — and every one of them stops at review.*

## Install into a repo

```bash
mkdir .task-manager && curl -L \
  https://github.com/12vectors/bench/releases/latest/download/bench.tar.gz \
  | tar -xz -C .task-manager
./.task-manager/start.sh        # wires the project (idempotent) and serves
```

No token, no clone: releases are curated artifacts that never contained
bench's own cards or settings, so the board starts empty by construction.

The first run asks the two questions it cannot answer for you — solo or
team, and which agent adapter — and writes `manager/local/.env` from the
documented example, so every other setting is discoverable in your own
copy. What runs your tests is read off the project rather than asked
(`package.json` → `npm test`, and so on); nothing recognisable leaves
`BOARD_AGENT_COMMANDS` empty, and that is the key to set before an agent
can run them. Bare Enter takes the default
throughout; with no terminal (CI, a script) it asks nothing and
`install.py --setup` asks later.

Commit `.task-manager/` into the host repo — core is vendored on purpose,
so clones work offline and updates show up in the host's own diffs.

The workflow brief ships as `.task-manager/AGENTS.md` — the cross-vendor
name coding agents read natively — with `CLAUDE.md` beside it as a one-line
compatibility pointer. Both live inside `.task-manager/`, so a host repo's
own root `AGENTS.md` is never touched.

## Install outside the repo

Bench does not have to live in the repository it manages. Name an
application and a work state of its own and nothing of bench — not the
tool, not a task file, not one bookkeeping commit — ever appears in that
application's history. Four locations, each named separately:

```text
workspace/                       # a grouping directory, not a repo
├── app/        BOARD_APP_ROOT   # the application + its own origin
├── bench/                       # this install: tool and configuration
├── work/       BOARD_WORK_ROOT  # tasks/, plans/, reference/ — its own repo
└── worktrees/  BOARD_WORKTREES  # the application's task branches
```

```bash
mkdir -p /ws/bench && curl -L \
  https://github.com/12vectors/bench/releases/latest/download/bench.tar.gz \
  | tar -xz -C /ws/bench
python3 /ws/bench/install.py --app /ws/app --work /ws/work
/ws/bench/start.sh
```

`--app` and `--work` must be absolute: bench resolves nothing against a
working directory it cannot see. The install writes both into
`manager/local/.env`, builds the work state's skeleton and wires the
agent adapter — and refuses the whole layout, with the reason, rather
than falling back to a repository nobody named. `--worktrees` moves the
task branches' checkouts; `--app-hooks` additionally writes the
application's *local* settings file, and refuses if git tracks it.
`--dry-run` reports and writes nothing.

Two git identities, and they never cross. Branches, worktrees, pushes and
code PRs are the application's; creating, moving and archiving a ticket
is the work state's. The tickets are local-only until you `git init`
their root, versioned by the board once `BOARD_COMMIT_MOVES` is on, and
shared with `BOARD_SYNC` through the work state's own
`BOARD_STATE_GIT_REMOTE` and `BOARD_STATE_BASE`. A card moving columns
cannot commit on the application's base, a code PR cannot target the
work-state remote, and merging into the application's base is still your
click. Runtime state — agent logs, session timelines, the generated
hooks, the recorded execution plans and their machine-local paths —
stays with the tool and is committed nowhere.

The whole of it, including the fail-closed validation, external hook
wiring, how a card's branch is planned in the application's repository
and how a stable operator install manages a candidate bench checkout, is
the "Running outside the application" section of
[AGENTS.md](AGENTS.md) — and on the docs site, which is cut from it.

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
any particular app (drivers do: `manager/local/driver/start`), agent
vendor (adapters do: `manager/core/adapters/`), or project
(`manager/local/` does).

The consequence is what makes an update safe: `update.sh` replaces
`manager/core/` wholesale, and everything a project taught bench about
itself lives outside it. Full docs in [AGENTS.md](AGENTS.md) — the file
the docs site is cut from, and the one an agent working in your repo
reads; the adapter contract in
[manager/core/adapters/README.md](manager/core/adapters/README.md).

## License

[MIT](LICENSE).
