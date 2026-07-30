# Task Workflow

Tasks move through a kanban of directories. The directory a file sits in **is**
its status — there is no other source of truth.

```
.task-manager/
├── AGENTS.md             ← This file (core-owned; replaced by updates)
├── CLAUDE.md             ← Compatibility pointer to AGENTS.md — nothing else
├── install.py            ← Wires the project via the agent adapter (see below)
├── start.sh              ← One-command start: install + port handling + board
├── stop.sh               ← Safe stop: refuses while agents run (--force overrides)
├── update.sh             ← Replace core/ from the published release; local/ survives
├── tasks/                ← Task state, nothing else. Works as a plain folder
│   ├── backlog/…done/    ←   kanban even if manager/ is deleted or ignored.
│   └── archive/          ← Archived cards: out of the flow, never deleted
├── plans/                ← Claude Code plan files (via plansDirectory setting)
├── reference/            ← Supporting documents referenced by tasks
└── manager/
    ├── core/             ← The tool. Replaced WHOLESALE by update.sh — never
    │   │                    put anything project-specific here.
    │   ├── VERSION, board.py, config.py … httpd.py, board.html
    │   ├── prompts/      ← Default agent prompt templates
    │   ├── adapters/     ← Agent-vendor integrations (claude/ and
    │   │                    opencode/ ship; contract in README)
    │   └── driver.example/
    └── local/            ← This project's half. Updates never touch it.
        ├── .env          ← Settings (gitignored; defaults in core/.env.example)
        ├── AGENTS.md     ← Project-specific workflow notes — read it too
        ├── CLAUDE.md     ← Compatibility pointer, as at the root
        ├── driver/start  ← How THIS project's app launches from a worktree
        ├── commands/     ← Project chores run against a task's worktree
        ├── prompts/      ← Prompt overrides (same filename beats the default)
        ├── adapters/     ← Adapter overrides/additions
        └── state/        ← Runtime data: sessions, agent logs, drives (gitignored)
```

Three layers, one law: **core knows about tasks, worktrees, PRs and events —
it knows nothing about any particular app, agent vendor, or project.**
Drivers know apps, adapters know vendors, `local/` knows this project.
`tasks/` holds only state and works as a plain folder kanban even if
`manager/` is deleted; the board narrates hand-moves when it happens to run.

Module map for `manager/core/` (dependencies flow strictly left to right):

```
config → state → taskfiles → events / github / drive → agents → watch / httpd → board.py
```

- `config.py` — paths, stages, settings, prompt/adapter/driver resolution
- `state.py` — shared registries, event log persistence, SSE fan-out
- `taskfiles.py` — reading and moving task files; the only code touching tasks/
- `events.py` — ingests NORMALIZED events (the adapter contract), session registry
- `github.py` — PR opening, Copilot requests, review/CI polling
- `drive.py` — runs the project driver, tracks the one live drive
- `agents.py` — headless work/review jobs, launched through the adapter
- `watch.py` — 2s disk poller narrating moves made outside the API
- `httpd.py` — HTTP routes, the SSE stream, serving the page
- `board.py` — argparse + startup wiring only

## Agent adapters

Headless jobs run through an adapter (`BOARD_AGENT_ADAPTER`, default
`claude`), so the manager works with other coding agents too. An adapter is
a directory with `run` (execute one job: `AGENT_PROMPT` + `AGENT_MODE`
work|act-pr|review + `AGENT_COMMANDS` in, stdout = the log, markers parsed
from it) and `wire` (idempotently give the host project live-session
visibility). Headless jobs answer no permission prompts, so each intent is
granted exactly the side effects its prompt demands — commit and test for
work, push for act-pr, posting PR verdicts for review — with the project's
own runnable commands coming from `BOARD_AGENT_COMMANDS` as neutral
prefixes each adapter renders in its vendor's rule syntax. Adapters
translate their vendor's events into the board's normalized schema at the
edge — core never sees vendor payloads. The full contract, including the
event schema, lives in `core/adapters/README.md`.

## Drives

The **⛭ drive** chip on a review card launches the app locally *from that
task's worktree*, so you can click around the actual feature before
merging. How an app starts is project knowledge, so it lives in the
project's driver — `local/driver/start`, an executable the board runs and
owns: refuse fast with a printed reason, print `DRIVE URL: <url>` when up,
run until parked (SIGTERM). No driver → the chip says so and the tooltip
explains what to create; `core/driver.example/` documents the contract.
One drive at a time; **park** takes it down.

## The activity bar and the archive

The bottom bar is one place: what happened, and where things go. **Activity**
expands the full event log (filters, the plans/ and reference/ listings, a
resize grip); collapsed, the latest event ticks along the bar. The
**Archive** tray anchors the right end — drag a card from `backlog/`,
`to-do/` or `done/` anywhere onto the bar and it moves to `tasks/archive/`:
out of every column, never deleted, Status set to `Archived`. The toast says
⌘Z brings it back, and it does — nothing in this system removes work
without an undo in the same breath. Cards in the working stages
(in-progress, review) cannot be archived; finish or walk them back first.

## Local commands

Projects grow chores that belong to a specific checkout — applying a
branch's DB migrations, reseeding, rebuilding assets. Those are
**local commands**: executables in `manager/local/commands/`, surfaced as
`$`-glyph chips on cards that have a branch (in progress and review) and
run against that task's worktree (recreated from the branch if needed).
The contract mirrors the driver's: env in (`CMD_WORKTREE`, `CMD_BRANCH`,
`CMD_TASK`, `CMD_REPO`), output to a log under `local/state/commands/`,
and the ticker narrates the ending either way with the log's last line.
A `# help:` line near the top of the script becomes the chip's tooltip.
Commands arm on first click and run on the second.

## Updating

`./.task-manager/update.sh` downloads the latest GitHub Release of the
distribution repo (`BENCH_REF=v3` pins a tag; the source is stamped into
the script at build time, `BENCH_SOURCE` in `local/.env` overrides it),
replaces `manager/core/` wholesale plus the top-level files named in the
artifact's `manager/core/release-manifest`, and touches nothing else —
tasks, driver, prompt overrides, `.env` and state all survive. No release
published → it says so and changes nothing; developers working on bench
itself update their clone with git instead. Then re-run `install.py`
(idempotent re-wire) and restart the board. Releases are cut from the
bench repo with `release.sh` (never shipped in the artifact): it builds
the tarball from the manifest, tags `v<VERSION>` and publishes via `gh`.

## Installing into a project

```bash
python3 .task-manager/install.py             # idempotent; --dry-run to preview
```

Checks that the containing directory is a `.claude`-initialised project and
delegates to the configured agent adapter's `wire` — for Claude that means
`.claude/settings.json`: `plansDirectory` and the five event hooks running
the adapter's `emit.py`. Fully present → reports "ok" and touches nothing;
partial, stale (old `.tasks/` paths) or duplicated → repaired in place. Other
hooks and settings are never touched, so it is safe to run any time — e.g.
after dropping `.task-manager/` into a new repo.

## Seeing the board

```bash
./.task-manager/start.sh                 # the usual way: install + port + board
python3 .task-manager/manager/board.py   # or run the server directly
```

`start.sh` runs `install.py` (idempotent), then sorts out the port before
serving in the foreground (Ctrl-C stops it). Three cases:

- this project's board already answers on the port → just reopens the browser
- the port is free → starts on it
- something else occupies it → takes the next free port **and persists it to
  `manager/.env`**, so the hooks and agents — which read the same file —
  follow the board rather than reporting to a port it no longer serves.

Extra arguments pass through to `board.py` (e.g. `./start.sh --no-open`).

`stop.sh` is the counterpart. It identifies the board by asking the port's
API for its tasks root, so it never kills a foreign process squatting there.
While agents are running it refuses — stopping the board loses their endings
(auto-move, PR opening, decline handling) even though the agent processes
themselves survive — and names who is working on what; `--force` overrides.

Port **26071 is pinned** so the URL is always the same one to bookmark. Running
the command again while it is already up just reopens that tab rather than
failing on a port clash.

All settings live in `manager/core/.env.example` with their defaults documented —
the port, the binaries agents launch with, the commands agents may run,
the worktrees directory, whether moves claim and commit themselves, the
watch interval and the in-memory caps. Copy it to `manager/local/.env`
(gitignored) to override locally; real environment
variables beat `.env`, which beats the defaults. The hook bridge reads the
same `.env`, so changing `BOARD_PORT` moves the board, the agents and the
hooks together.

Stdlib only, no install. It reads the directories on every request, so refreshing
the page shows current disk state. Dragging a card between columns does both
steps of a move for you — it renames the file and rewrites its **Status:** line.
Cards whose Status line disagrees with the directory they sit in are flagged
`status drift`.

## Live view

The UI follows the **Bench** design system — cool sea neutrals, IBM Plex Sans
for anything a person wrote and Plex Mono for anything a machine produced,
and colour that only ever means state: `--accent` (surf) an agent alive,
`--calm` (pine) settled or passed, `--alarm` (terracotta) blocked, failed or
HIGH, `--idle` (driftwood) done. The one looping animation ("breathe") means
an agent is working; a blinking caret means output is still arriving. Night
theme by default; the header button switches to Daylight. Tokens live at the
top of `manager/board.html`.

The board has three views (header switcher):

- **Board** — the kanban, live. Active Claude Code sessions appear as chips in
  the header; a card an agent is working on carries a live activity line; the
  bottom ticker narrates the latest events and every move is attributed
  (`you` / `agent` / `disk`).
- **Sessions** — a flight recorder per session: a chronological timeline of
  reads, edits, test runs, commits and card moves, with filters and expandable
  output. Sessions persist to `.sessions/*.jsonl`, so past ones can be replayed.
- **Focus** — a heads-up display for one session: the task it holds, its live
  TodoWrite plan, the project's configured definition-of-done checks (a
  `checks` file in `manager/local/` overriding the shipped default in
  `core/`), and per-file diff stats from its worktree.

Liveness comes from Claude Code hooks configured in `.claude/settings.json`:
every session in this repo POSTs normalized events to the board via the
claude adapter's `emit.py` (fails silently in under a second when the board isn't
running). A watcher thread also polls the stage directories every 2s, so moves
made by hand still show up. The browser gets everything over SSE — no refresh
needed. Hooks are snapshotted at session start, so a session already open when
the hooks were added won't report until restarted.

Agent prompts ship in `manager/core/prompts/` and can be overridden per
project by placing a file of the same name in `manager/local/prompts/`
(the override wins). They are plain
markdown with `{branch}` / `{stage}` / `{filename}` / `{body}` placeholders,
filled via `str.format` — so literal braces elsewhere in a prompt would break
it. They are read fresh on every agent launch; edits apply without restarting
the board.

## Agents working the board

Card actions appear on hover, taking over the status pill's slot (never
stacking on top of it) — at most two per state, only things you'd actually
do without opening the card: **▸ start work** on in-progress cards,
**‖ hold** while an agent runs, **↩ back** on cards waiting on you,
**↺ reopen** on done cards, and **◔ still true?** everywhere. Actions that
cost tokens or stop work arm on first click and fire on the second.

Each launched agent wears a short name for its lifetime (Wren, Juno,
Basil, …) — picked per launch, never shared by two running agents, shown as
`Wren · #09` on cards, in the sessions list and throughout the ticker. Names
are held in memory, so a restarted board falls back to plain "Agent" for
sessions that predate it.

**▸ start work** launches a headless `claude -p` on the task. It exists only
on `in-progress/` cards: moving a card to in-progress is the commitment, and
only then does work start — the server refuses launches from anywhere else.

1. The board creates a git worktree at `.worktrees/<task-stem>/` on a new
   branch `task/<task-stem>` from the newest main it can see: with an
   `origin` remote it fetches `origin/main` first (bounded by
   `BOARD_FETCH_TIMEOUT`) and branches from that; no remote, a failed
   fetch or a timeout fall back to current HEAD, so launching never
   waits on the network. The main checkout itself is never touched, and
   the ticker names the branch point whenever it isn't just HEAD. (The
   agent is told not to touch the task file — worktree moves would be
   invisible to the main checkout anyway.)
2. The agent works in the worktree: implements, tests, commits. Its hook
   events stream to the board like any session.
3. On clean exit with commits on the branch the board moves the card to
   `review/`; on failure it stays in `in-progress/` and the exit is narrated
   in the ticker. A clean exit that committed *nothing* also stays in
   `in-progress/` and is called out loudly — an empty branch reaching
   review/ is how a broken launch hides. Stdout is kept in `.agent/logs/`.

## Pull requests

A card entering `review/` with a `task/<stem>` branch gets a PR opened for
it automatically — mechanically, by the board, not by an agent: it pushes
the branch to the repo's remote and runs `gh pr create` with the task title
and the agent's closing summary as the body. The PR url is written into the
task file as a `**PR:** <url>` line, so the file stays the source of truth
and the card grows a `PR ↗` chip. Cards without a branch pass through
quietly. One guard is loud: if local `main` is ahead of the remote, the PR
would drag those commits into its diff, so the board refuses and tells you
to push main first (then move the card out and back, or wait for the next
entry into review/).

Review-stage cards with a PR carry two actions:

- **◔ review PR** — a read-only agent reads the full diff in context,
  checks it against the task and AGENTS.md, posts its verdict to GitHub
  (`gh pr review --approve` / `--request-changes`) and appends a
  `## PR review` section to the task file ending in
  `PR REVIEW: APPROVE | REQUEST CHANGES`.
- **⚑ copilot** — requests a GitHub Copilot review via the API. Works iff
  Copilot code review is enabled for the repo; the error is relayed to the
  toast if not. The card tracks the whole arc with a `⚑` chip: `⚑ ◌` asked,
  `⚑ ✓` approved, `⚑ ✕` changes requested, `⚑ ·` commented — "asked"
  becomes a verdict when the pending request turns into an authored review.

Once any review is in (a verdict from either agent kind, Copilot, or a
human), the card's actions shift to the loop that matters then:
**↻ act on PR** replaces the copilot button — an agent re-enters the task's
worktree (recreated from the branch if it was cleaned up), reads every
review and line comment, addresses each point or says why not, commits,
pushes so the PR updates, and appends a `## PR update` section to the task
file. Then **◔ review PR** again, until it settles.

The board polls open PRs of review-stage cards (reviews + CI checks +
GitHub's mergeable state, every 60s — a plain thread in board.py, no agent
involved, silent when review/ is empty) and folds everything into one
verdict — any changes-requested review or failing check wins over any
approval. A PR GitHub cannot merge cleanly wears an alarm-coloured
`conflicts` chip and counts as changes-needed-by-you (not a CI failure);
**↻ act on PR** resolves mechanical conflicts by merging main into the
branch — additively, never rebasing or force-pushing — in a dedicated
resolution commit, and refuses semantic ones, naming the collision for a
human to settle. GitHub computes mergeability lazily, so an UNKNOWN
reading keeps the chip's last state rather than flapping.
Tool chips (CI, copilot, PR, drive)
are destinations, not statuses: they live in the card's footer row, never
squeezed into the author row — `CI ✓` (pine), `CI ✕` (terracotta), `◌`
while in flight, with hover actions staying in the status pill's slot. The card wears it in the design
system's state colours: approved → pine (`--calm`) border and an
`approved` pill; changes asked → terracotta (`--alarm`) and a
`changes asked` pill; otherwise it stays the neutral `waiting on you`.
Merging remains yours — the board never merges.

The agent's first duty is to judge whether the task is actionable. If the
task still has open questions — unresolved decisions only its author can
settle — the agent does no work and exits with a `NOT READY: <reason>`
marker. The board then moves the card back (to `to-do/`, or `backlog/` if it
started there), records the reason in the ticker, and deletes the untouched
worktree and branch so the task can be refined and relaunched cleanly.

**◔ still true?** (every stage, done included) fires a read-only relevance
agent instead: no worktree, edit tools disallowed, running in the main
checkout. It checks the task against the actual codebase — already done?
assumptions stale? still worth doing as written? — and its report is
appended to the task file under a `## Relevance review — <date>` heading,
with the verdict (`Still relevant | Partly done | Already done | Needs
rewrite`) in the ticker. The card does not move; deciding what to do with
the verdict is yours. One agent per task at a time applies across both
kinds.

Dragging a card with work attached (branch or PR) to `done/` opens a
three-way choice instead of just moving: **keep it where it is** (nothing
changes), **just move the card** (branch, PR and worktree stay), or
**merge & clean up** — park the drive if it is this task's, merge the
branch into main, push (which marks the PR merged) and delete the remote
branch, remove the worktree and local branch, then move the card. Every
step narrates in the ticker; a merge conflict aborts cleanly and the card
stays put. Cards without work move silently, and hand-moves on disk are
never intercepted — the board only asks when you act through it.
One agent per task at a time; a work agent's worktree must not already
exist when starting.


The flow is linear:

```
backlog → to-do → in-progress → review → done
```

## Stages

### backlog/
Where new tasks are written and where they wait. A backlog task may be rough,
incomplete, or fully specified — what it has in common with its neighbours is
that nobody is working on it. Most tasks live here for most of their life.

### to-do/
Picked up and queued to work on next. Moving a task from `backlog/` to `to-do/`
is a commitment to do it soon, so keep this directory short — a long `to-do/` is
just a second backlog.

### in-progress/
Actively being worked on right now. Anything here should have someone (or an
agent session) attached to it. If work stalls, move it back to `to-do/` or
`backlog/` rather than leaving it parked — a stale `in-progress/` makes the board
lie about what is happening.

Implementation plans (created via Claude Code's plan mode) are stored in `plans/`
and can be referenced from the task file.

### review/
The work is built and awaits judgment: tests written and passing, a PR open
(see "Pull requests"), behaviour checked in the running app, edge cases
probed. A task sitting here has code but not yet confidence. If review turns
up problems, move it back to `in-progress/`.

### done/
Finished and merged. Completed task files are kept as a record of what was built
and why — they are the closest thing we have to design history, so don't delete
or trim them.

### reference/ (beside tasks/, not a stage)
Supporting documents that tasks can link to — external specs, API documentation,
research notes, screenshots, competitive analysis, regulatory references, etc.
These don't move through the workflow; they're stable resources. Reference them
from task files using relative links — two levels up from a stage directory
(e.g. `[IVASS spec](../../reference/ivass-document-requirements.md)`).

## Moving a task

When moving a task between stages:
1. Update the **Status** field in the task file header
2. Move the file to the new directory
3. Add any notes about why it's moving (e.g. "approach agreed, starting build")

Moves are not always forward. Going back a stage is normal and expected —
verification failing, or an approach not surviving contact with the code, should
move the task backwards rather than being worked around in place.

## Claiming a card

**Claiming is moving.** Taking a card out of `backlog/` or `to-do/` towards
work is the commitment, so that is where ownership is recorded: the board
writes an `**Assignee:** <name>` line into the header, taken from this
checkout's `git config user.name` — the identity git history already shows,
no new concept. The first claim sticks: a card that already names an
assignee keeps it when someone else moves it on. Walking a card all the way
back to `backlog/` clears the line — nobody holds it again.

The assignee is who launches agents on the card and whose judgment the
review waits for. It is a convention, not a lock: the board does not (yet)
refuse anyone else's actions.

Two consequences worth knowing:

- **Hand-moves bypass the claim.** A plain `mv` between stage directories
  is still a first-class move (the watcher narrates it), but nothing writes
  the assignee — update the line yourself in the same edit as **Status**.
- **Identity is git's, so it collides like git's.** Two machines both
  configured `user.name = ronald` are one person as far as the board is
  concerned. Teams that share a git history already share that assumption.

With `BOARD_COMMIT_MOVES` on, board-made moves also **commit themselves**:
the move and the claim land in one commit touching only that task file,
messaged `board: <number> → <stage> (<name>)`, staged by pathspec so
unrelated staged work is neither committed nor unstaged (hooks are skipped —
this is bookkeeping, not code). Pushing is not part of it: those commits sit
on your local `main` until you push it, which the PR guard above will tell
you about if you forget. The setting is off by default: a single-player
board neither writes nor clears the assignee and makes no commits, exactly
as before, and `tasks/` is committed by hand. The gate governs only whether
a *move* writes the line — an **Assignee:** added to a file by hand is still
read and shown on the card whether the gate is on or off.

## Task file format

Each task is a markdown file with a descriptive filename
(e.g. `01-document-handling-review.md`). Numbers are allocated in creation order
and stay with the file for life — they do not renumber when a task moves stage.

Start new tasks from `tasks/task-template.md` — copy it into `backlog/` and
fill it in. Its sections earn their keep: Context and What-to-build are what
a work agent gets as its brief, Acceptance is what reviews judge against,
and a non-empty Open-questions section makes an agent refuse the task
(`NOT READY`) rather than guess. The template itself is never listed on the
board (only stage directories are read).

The file should have at minimum:

```markdown
# Task title

**Status:** Backlog | To Do | In Progress | Review | Done
**Priority:** High | Medium | Low
```

Use those exact status values — nothing else (not "Not started", "WIP", etc.) —
and keep the status in step with the directory the file sits in. Priority may
carry a short justification after the level
(e.g. `Medium — foundational for any real environment`).

An optional **Type** line can record what kind of work the task is, when that
isn't obvious from the title:

```markdown
**Type:** Discovery | Bug | Feature | Refactor | Chore
```

Type is orthogonal to status. A discovery task — research, scoping, spiking an
approach — moves through the same five stages as everything else; "discovery"
describes the work, not where it sits on the board.

An optional **Assignee** line records who holds the card:

```markdown
**Assignee:** ronald
```

The board writes it when a move claims the card (see "Claiming a card") and
removes it when the card is walked back to `backlog/`; on `done/` cards it
stays as history. Editing it by hand is fine — it is a plain header field,
and a hand-move should update it alongside **Status**.

An optional **Depends on** line can name what must land first — task numbers
or external preconditions — so sequencing lives in the header instead of
prose asides:

```markdown
**Depends on:** 03, 05
```

The board does not enforce it; it informs whoever picks the next card.

The rest of the file is freeform — description, research findings, approach,
open questions, whatever is relevant to the current stage.
