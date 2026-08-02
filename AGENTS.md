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
config → state / reports → taskfiles → events / github / drive / sync → agents → phases → watch / httpd → board.py
```

- `config.py` — paths, stages, settings, prompt/adapter/driver resolution
- `state.py` — shared registries, event log persistence, SSE fan-out
- `reports.py` — what the record keeps of an agent's closing report: one
  cap, one clip, for the task file and the PR body alike
- `taskfiles.py` — reading and moving task files; the only code touching tasks/
- `events.py` — ingests NORMALIZED events (the adapter contract), session registry
- `github.py` — PR opening, Copilot requests, review/CI polling
- `drive.py` — runs the project driver, tracks the one live drive
- `sync.py` — origin/main as the shared board: push on move, pull on a beat
- `agents.py` — headless work/review jobs, launched through the adapter
- `phases.py` — a phase run: its own branch, its cards merged into it one
  at a time. A beat, not an agent; it holds no registry of where a phase is
- `watch.py` — 2s disk poller narrating moves made outside the API, and
  the gate that keeps a move a pull applied from triggering anything
- `httpd.py` — HTTP routes, the SSE stream, serving the page
- `board.py` — argparse + startup wiring only

## Agent adapters

Headless jobs run through an adapter (`BOARD_AGENT_ADAPTER`, default
`claude`), so the manager works with other coding agents too. An adapter is
a directory with `run` (execute one job: `AGENT_PROMPT` + `AGENT_MODE`
work|act-pr|review + `AGENT_COMMANDS` in, stdout = the log, markers parsed
from it) and `wire` (idempotently give the host project live-session
visibility).

Headless jobs answer no permission prompts, so each intent is granted
exactly the side effects its prompt demands — commit and test for work,
push for act-pr, posting PR verdicts for review — with the project's own
runnable commands coming from `BOARD_AGENT_COMMANDS` as neutral prefixes
each adapter renders in its vendor's rule syntax.

Adapters translate their vendor's events into the board's normalized
schema at the edge — core never sees vendor payloads. That is what keeps
the board's own code free of any one vendor: swapping adapters swaps the
binary that does the work, not the board. The full contract, including
the event schema, lives in `manager/core/adapters/README.md`.

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

The same action sits on the card, for the common tidy-up the length of the
board is too far to drag: a **⌸** chip at the right-hand end of the footer
row, wearing the tray's own glyph, arming on the first click and firing on
the second. It is there on exactly the cards the tray accepts — a working
card has no chip at all rather than one that refuses — and it is one
action, not two: the same request, the same ⌘Z toast, the same count on
the tray. Which stages may be archived from is answered once, by the
server that enforces it, and sent with the state; the drag gesture and the
chip both read that answer.

Archiving is a move, so it commits like one: under `BOARD_COMMIT_MOVES`
the rename into `tasks/archive/` lands in a single `board: <n> → archived
(<name>)` commit naming both paths, and ⌘Z commits its own way back. That
is not bookkeeping for its own sake — an uncommitted deletion of a tracked
file is exactly what `BOARD_SYNC` refuses to run over, so an archive that
stopped at the disk would silently hold up every later move on that board,
and the archived card would exist in one working tree only. The same law
covers every write the board makes to a task file: the `**PR:**` line, a
claim, and an agent's closing report all reach git the same way.

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
python3 .task-manager/install.py --setup     # ask the settings questions again
```

Checks that the containing directory is a `.claude`-initialised project and
delegates to the configured agent adapter's `wire` — for Claude that means
`.claude/settings.json`: `plansDirectory` and the five event hooks running
the adapter's `emit.py`. Fully present → reports "ok" and touches nothing;
partial, stale (old `.tasks/` paths) or duplicated → repaired in place. Other
hooks and settings are never touched, so it is safe to run any time — e.g.
after dropping `.task-manager/` into a new repo.

### The first run writes local/.env

A project with no `manager/local/.env` runs on the documented defaults, so
the two settings that change what bench *is* — claim-on-move and syncing
through origin/main — stay invisible to anyone who has not read
`core/.env.example`. So the first run asks, and writes the file: **solo or
team** (team turns `BOARD_COMMIT_MOVES` and `BOARD_SYNC` on together, and
the question says what that costs) and **which agent adapter**
(enumerated from the adapter directories, so a project's own
`local/adapters/` entry is offered).

It does not ask what runs the project's tests. That was a third question
once, and it wanted an answer about a repo the person may have just
cloned, thirty seconds in and before anything had explained why the
board needed one. It is detected instead — `package.json` → `npm test`,
`Cargo.toml` → `cargo test`, `go.mod` → `go test ./...`, a
`pyproject.toml`/`setup.py`/`tests/` → `python3 -m unittest` — and a
project matching none of them gets `BOARD_AGENT_COMMANDS` empty, which
is honest: an agent then runs no project commands until someone fills it
in. A wrong guess costs nothing an empty value would not, since a prefix
that matches nothing denies exactly the same way. An existing value is
never overwritten, so `--setup` cannot undo a hand-edit.

Bare Enter takes the default, Ctrl-D skips the rest, and what lands is
`core/.env.example` with the answers substituted into their lines: every
other key, every comment, so the written file is where the project reads
what else it can change. The cost of writing the whole example is that it
snapshots it — an update that adds a key does not add it to your copy.

It runs **after** the first-boot clean, because `.env` is one of the two
things the first-boot guard reads. It never asks without a terminal on
stdin: `install.py` sits on the path of `start.sh`, `update.sh` and every
hook, so no TTY prints one line (defaults apply, `--setup` asks) and
carries on rather than blocking a board start with a prompt nobody can
see. `--dry-run` reports the questions and writes nothing. An existing
`.env` is never touched — no repair, no merging in new keys — and
`--setup` is the only way back to the questions, offering the current
file's values as the defaults and rewriting it in place.

## Seeing the board

```bash
./.task-manager/start.sh                 # the usual way: install + port + board
python3 .task-manager/manager/board.py   # or run the server directly
```

`start.sh` runs `install.py` (idempotent), then sorts out the port before
serving in the foreground (Ctrl-C stops it). Three cases:

- this project's board already answers on the port → just reopens the browser
- the port is free → starts on it
- something else occupies it → waits a few seconds for it to clear, then
  takes the next free port **and persists it to `manager/local/.env`**, so
  the hooks and agents — which read the same file — follow the board rather
  than reporting to a port it no longer serves. Overwriting a port the user
  pinned is not done quietly: the hop names the file, both ports and how to
  reclaim the old one.

The probe behind those three cases binds the way the board itself binds —
`127.0.0.1` with `SO_REUSEADDR`, which `ThreadingHTTPServer` sets — so the
socket a just-stopped board leaves in `TIME_WAIT` does not read as
occupied. A probe stricter than its server would hop a restart off its own
pinned port. The wait on top covers the rest of a predecessor's shutdown —
five seconds, or whatever `BOARD_PORT_WAIT` says in start.sh's environment
— because a restart races its own board far more often than a stranger
takes the port.

Extra arguments pass through to `board.py` (e.g. `./start.sh --no-open`).

`stop.sh` is the counterpart. It identifies the board by asking the port's
API for its tasks root, so it never kills a foreign process squatting there.
While agents are running it refuses — stopping the board loses their endings
(auto-move, PR opening, decline handling) even though the agent processes
themselves survive — and names who is working on what; `--force` overrides.

Port **26071 is pinned** so the URL is always the same one to bookmark. Running
the command again while it is already up just reopens that tab rather than
failing on a port clash.

Since a second bench means a second tab, the tab title names its project:
`<project> · bench` — the project first, because tab truncation eats the
tail and the tail is the same in every bench tab. The project is the repo
directory's name unless `BOARD_TITLE` in `local/.env` overrides it. The
server renders it into the page, so it is right on first paint; switching
view swaps only the tail (`<project> · sessions`).

While agents are running the title leads with how many — `2◌ · <project>
· bench` — because a backgrounded tab is the only place that state can
still be read, and the reasoning that put the project first applies
harder to it: a tab narrowed to a few characters still shows the count.
It is the same list the header's live chip counts, so the two can never
disagree, it rides every view, and a board with nothing running has
exactly the plain title above, byte for byte.

All settings live in `manager/core/.env.example` with their defaults documented —
the port, the binaries agents launch with, the commands agents may run,
the worktrees directory, whether moves claim and commit themselves,
whether boards sync through origin/main and how often, the
watch interval and the in-memory caps. The first `install.py` copies it to
`manager/local/.env` (gitignored) with a few answers substituted in — see
"The first run writes local/.env" above — and that copy is where a project
overrides anything; real environment
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

- **Board** — the kanban, live, and only what you are holding yourself: a
  phase's members are drawn elsewhere, with the phase card standing for
  them and each column saying how many it is not showing (see "A phase's
  members leave the Board view"). Active Claude Code sessions appear as chips in
  the header; a card an agent is working on carries a live activity line; the
  bottom ticker narrates the latest events and every move is attributed
  (`you` / `agent` / `disk`, or the teammate's git name when a sync brought
  it). With `BOARD_SYNC` on, a second header chip appears — and only
  appears — when sync stops converging, saying whether it is behind
  (origin unreachable, self-healing) or stalled on something only a human
  can settle.
- **Sessions** — a flight recorder per session: a chronological timeline of
  reads, edits, test runs, commits and card moves, with filters and expandable
  output. Sessions persist to `local/state/sessions/<id>.jsonl`, so past ones
  can be replayed — and beside each log sits `<id>.who.json`, who that
  session was: the agent id, its name, the model it rode and its task. The
  name and the model are nowhere in the event stream, so that file is the
  only thing a restart can read them back from, and it is what keeps a
  replayed agent run wearing its own name and model chip rather than the
  person's label. Three labels, three states, and each says only what is
  known: the agent's name, `You` for a session recorded as carrying no
  agent, and a neutral `Session · <id>` for a log written before any of
  this was recorded. `You` is never a guess.
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
do without opening the card: **▸ start work** on in-progress cards (**▸
take over** when someone else holds them), **‖ hold** while an agent runs,
**↩ back** on cards waiting on you, **↑ open PR** on review cards whose
branch has none, **↺ reopen** on done cards, **⟶ phase** on unstarted
cards a phase in `to-do/` could take, and **◔ still true?** everywhere.
Actions that cost tokens or stop work arm on first click and fire on the
second.

Each launched agent wears a short name for its lifetime (Wren, Juno,
Basil, …) — picked per launch, never shared by two running agents, shown as
`Wren · #09` on cards, in the sessions list and throughout the ticker. Names
are held in memory, so a restarted board falls back to plain "Agent" for
sessions that predate it.

Beside that name, wherever it identifies a run — the sessions list, the
session and Focus headers, the working card's agent line — sits the model
the launch rode: a small mono chip in the id hash's dim register
(`opus-4-8`, the vendor's whole string on hover). Which brain did the work
is a review question, not a state, so the chip takes no colour. A launch
that inherited the vendor default, and a session replayed from disk, wear
no chip at all — the board says nothing rather than guessing.

**▸ start work** launches a headless agent run on the task, through the
configured adapter. It exists only on `in-progress/` cards: moving a card
to in-progress is the commitment, and only then does work start — the
server refuses launches from anywhere else. In team mode it also refuses a
card someone else holds, naming them; the action reads **▸ take over**
there, and firing it is the deliberate reassignment. An unclaimed card
claims itself on launch. One agent per task at a time, and a work agent's
worktree must not already exist when it starts.

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
   `review/`; on failure it stays in `in-progress/` and the card wears the
   failure (below). A clean exit that committed *nothing* also stays in
   `in-progress/` and is called out loudly — an empty branch reaching
   review/ is how a broken launch hides. Stdout is kept in
   `local/state/agent/logs/`.

### What the record keeps of a report

The agent's closing report is the permanent record: it is appended to the
task file, shown as the session's last entry, and carried into the PR
body — the same text in all three, from one helper (`reports.py`) with
one cap. A report that fits arrives whole. One that doesn't keeps **both
ends** — the headline the report contract puts first, and the pointer
that closes it — and loses the middle, cut on line boundaries, with one
line of prose in its place saying how much went and naming the log under
`local/state/agent/logs/` that still holds all of it. The reader is never
left to infer that something was removed. A *failed* run is the deliberate
exception: its excerpt keeps the log's tail, because for a crash the end
is the story.

### A run that died

An agent that exits non-zero is the one outcome a person must not miss, so
it is a **state the card wears**, not an event that scrolls past. The run's
record keeps the exit code, when it ended, and the cleaned tail of its log
— the excerpt, which for an API outage is the whole story ("API Error:
500 …") and which a launch that died before the agent ever spoke still
answers honestly. From that the board does three things: the card takes the
`--alarm` border and a `run failed` pill, with the excerpt on hover and in
full in the card sheet; a toast fires, because failures are rare and
actionable; and the ticker keeps its line, now naming what the log ended on
rather than pointing vaguely at a file. Every headless kind lands here —
work, act-pr, PR review, relevance check — and a card that is not in
in-progress wears it just the same.

The state is scoped to the run and the stage: the next launch supersedes it
(the card reads its most recent run), and moving the card to another stage
drops it, since the failure was about the work in the stage it died in.
Nothing retries by itself — a dead run is a human decision point, and an
outage would make auto-retry a thundering herd — but the way is cleared for
the human: a failed run that committed nothing has its worktree and empty
branch removed, exactly as a decline does, so **▸ start work** is one click
again. A failed run *with* commits keeps its worktree; there is work in it.

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

The board that opens it is the one whose user moved the card (see "State
syncs; reactions don't"), and the `**PR:**` line is the backstop behind
that: it is checked before every `gh pr create`, in team mode it commits
itself so it reaches the other boards, and a create that races anyway
adopts the PR GitHub already has rather than failing. A review card that
has a branch but no PR carries an **↑ open PR** action — the way to ask
for one after the fact, since no board opens it behind your back.

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

A phase member's PR is opened against **its phase's branch**, not `main`:
its branch was cut from there, so that is the only base whose diff is the
member's own work — and a PR into `main` carrying a whole phase is exactly
the merge this design refuses to make. Every other card, the phase card
itself included, opens into `main` as it always did.

The card wears that verdict in the design system's state colours:
approved → pine (`--calm`) border and an `approved` pill; changes asked →
terracotta (`--alarm`) and a `changes asked` pill; otherwise it stays the
neutral `waiting on you`. Tool chips (CI, copilot, PR, drive) are
destinations, not statuses: they live in the card's footer row, never
squeezed into the author row — `CI ✓` (pine), `CI ✕` (terracotta), `◌`
while in flight — with hover actions staying in the status pill's slot.
Merging into `main` remains yours — the board never merges into `main`. It
does merge into a branch of its own: a phase's integration branch is the
board's, and merging into it is bookkeeping in the same family as
committing a move (see "A phase runs itself, on a branch of its own").

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

That work takes as long as it takes, so **the card wears it** rather than
sitting there looking idle while its branch is disassembled: from the
first step to the last it takes the accent border and a breathing
`completing` pill — the same vocabulary as an agent working, because that
is what is happening — and carries the latest narrated step on its
activity line ("parking the drive", "merged task/29-… into main",
"cleaned up: worktree and local branch removed"). While it does, it has
no hover actions, no drive or command chips and cannot be dragged, and a
second `complete` for the same card is refused rather than started. The
claim lives in this board's memory and is released on every exit —
merged, conflicted or crashed — so a failure gives the card straight back
and a board restarted mid-completion leaves nothing stuck. Other replicas
see the card unchanged until the move itself arrives.

With `BOARD_SYNC` on the merge is made **on origin** instead: the board
runs `gh pr merge` on the card's PR, cleans up and moves the card, and
local `main` fast-forwards to the result on the next beat. Replicas
converge only while main advances by fast-forward, so no board makes a
merge commit of its own. Two consequences the local path hid: whoever
clicks needs merge rights on the repo, not just push rights, and a branch
without a PR is refused with a pointer to **↑ open PR** — there is nothing
for origin to merge otherwise. Single-player merges locally, exactly as
above.

## Stages

The flow is linear:

```
backlog → to-do → in-progress → review → done
```

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
(e.g. `[the payments API spec](../../reference/payments-api.md)`).

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
review waits for. It gates exactly one thing — starting work, which
another board refuses until you take the card over deliberately (see
"State syncs; reactions don't"). Everything else is convention: reading,
reviewing and moving are open to anyone, and git history is the audit.

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
on your local `main` until you push it (or until `BOARD_SYNC` pushes them —
see "Syncing boards"), which the guard in "Pull requests" will tell you
about if you forget. The setting is off by default: a single-player
board neither writes nor clears the assignee and makes no commits, and
`tasks/` is committed by hand. The gate governs only whether
a *move* writes the line — an **Assignee:** added to a file by hand is still
read and shown on the card whether the gate is on or off.

## Syncing boards

`BOARD_SYNC=1` makes `origin/main` the truth and every board a converging
replica. It implies `BOARD_COMMIT_MOVES` — a move that never commits has
nothing to publish — and off (the default) nothing below runs: no fetch,
no push, no thread, no behaviour change at all.

- **Push is event-driven.** The commit a move makes is pushed as soon as
  it lands. A rejected push means another board got there first, so the
  board fetches, replays its own commits on top and pushes again.
- **Pull is a beat.** Every `BOARD_SYNC_INTERVAL` (30s by default) and
  once at startup: fetch, then fast-forward. The watcher narrates what
  arrived, attributed to the commit's author rather than `disk`.
- **Losing a race is a toast, not a mystery.** When replaying collides
  with a card someone else already moved, origin wins: the local move is
  dropped, the file reverts to origin's version and the board says
  `07 claimed by elena — your move was undone`.
- **A human's unpushed commit is never published.** Before any auto-push
  every local-ahead commit on `main` must be `board: `-prefixed. One
  that isn't stops the push (and the replay), names itself in the ticker
  and holds the header's `sync stalled` chip until you push it yourself
  or move it off main.
- **Offline is quiet.** An unreachable origin says so once, then works
  locally; commits queue on `main` and go out on the next reachable
  fetch.
- **Which remote is one answer, not two.** One remote and one branch, by
  design — but the remote is the one `BOARD_GIT_REMOTE` names, else the
  checkout's first, resolved in the same place PR opening asks, so the two
  halves of team mode can never publish to different places. It is used as
  named: a `BOARD_GIT_REMOTE` this checkout has no remote for stalls
  saying so rather than reaching past it for another one. A checkout with
  no remote at all stalls the same way, from startup — team mode syncing
  nothing is exactly the state a board must not render as healthy, and it
  is the likeliest first state of a fresh installation. Add a remote (or
  set the setting) and the chip clears with a line saying sync is
  converging again.

### State syncs; reactions don't

The board does not only render state, it reacts to it: a card entering
review opens a PR. With N replicas watching one truth, a reaction must
fire on exactly one of them, so **only the board whose user made the move
acts on it**. A move a pull applied renders and narrates — attributed to
its author — and triggers nothing. `watch.py` answers the question, since
that is where the attribution already lives, and every future automation
hung off a stage transition inherits it: am I the actor?

The file-carried gates stay in place behind that rule, so the rare double
is harmless rather than loud: the `**PR:**` line before `gh pr create`
(and a create that races anyway adopts the open PR), an existing branch
and worktree before a work launch. Both layers, deliberately — the
actor-only rule prevents the duplication, idempotency survives it.

Two consequences you can see:

- **A half-done side effect is nobody's to finish automatically.** The
  actor's board can die between moving a card and opening its PR; no
  other board picks that up, and in team mode the startup catch-up stands
  down for the same reason. The card wears **↑ open PR** instead — a
  person decides.
- **Ownership gates work launches.** A card someone else holds refuses
  **▸ start work**, naming them, and offers **▸ take over** as the
  deliberate second path. Shared liveness is not part of this: a
  teammate's running agent is a static "in-progress, assigned to them" on
  your board, because agent registries stay in each board's own memory.

Two disciplines make this safe, and team mode assumes both:

- **Local `main` advances only through the board and origin.** Code work
  lives in worktrees and PRs — that is what keeps the main checkout clean
  and fast-forwardable. Uncommitted changes to tracked files, a checkout
  sitting on another branch, or a divergence the guard won't replay all
  stall sync rather than risking your work; each one is narrated once and
  shown as a chip in the header until it clears.
- **Sync never merges.** It fast-forwards, or rebases the board's own
  bookkeeping commits. Nothing here force-pushes, and nothing reacts to
  what it pulled beyond narrating it.

One board fetches twice a minute at the default interval; N boards make
N times that. Against GitHub this is nothing, but on a rate-limited or
metered remote raise `BOARD_SYNC_INTERVAL`.

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

Every header field, and who writes it:

| Field | Required | Value | Written by |
| --- | --- | --- | --- |
| **Status** | yes | `Backlog` · `To Do` · `In Progress` · `Review` · `Done` (`Archived` for a card in `tasks/archive/`) | you, or the board on a move |
| **Priority** | yes | `High` · `Medium` · `Low`, optionally followed by a short justification | you |
| **Type** | no | `Discovery` · `Bug` · `Feature` · `Refactor` · `Chore` · `Phase` | you |
| **Assignee** | no | a name, taken from `git config user.name` | the board on a claiming move, or you by hand |
| **Depends on** | no | task numbers or external preconditions, comma-separated | you |
| **PR** | no | the pull request url | the board when it opens one |

**Status** is the field the board holds you to: a header that disagrees
with the directory the file sits in is flagged `status drift`.
**Assignee** and **PR** it writes and reads itself. The rest are for
whoever picks the next card.

An optional **Type** line can record what kind of work the task is, when that
isn't obvious from the title:

```markdown
**Type:** Discovery | Bug | Feature | Refactor | Chore | Phase
```

Type is orthogonal to status. A discovery task — research, scoping, spiking an
approach — moves through the same five stages as everything else; "discovery"
describes the work, not where it sits on the board.

### A phase is a card that lists its cards

A **phase** is a group of related tasks meant to run one after another. It is
not a directory, a stage or a registry — it is a task card like any other,
marked `**Type:** Phase`, with a `## Cards` section naming its members in the
order they run:

```markdown
## Cards

- 31 — Stand up site/ and its build
- 32 — Serve it from a Cloudflare Worker
- 33 — The landing page
```

The name of the phase is the card itself, its number and title, so nothing is
named twice. Document order is run order. The number is what is parsed — `31`,
`#31` and `031` are the same card — and whatever follows it is for the reader,
never matched against anything.

Membership runs one direction only: the phase card lists its members, and a
member card says nothing about phases. So membership cannot disagree with
itself, there is exactly one place to edit when it changes, and a member's
phase and position are *derived* — a `⟶ <phase> 3/5` chip in the card's footer
row, beside `CI` and `PR ↗`, opening the phase card.

What the list does not resolve is flagged on the card, in the same spirit as
`status drift`: a number no card has, a card two phase cards both list (both
are flagged), a card one phase lists twice, a line naming no number, and a
phase listed by a phase — phases do not nest. Each is an authoring mistake
that would otherwise surface later as a runner behaving oddly.

`**Depends on:**` is the other half, and it guards rather than orders: the
list says what runs next, a member's dependencies say whether it *may*. The
board parses the numbers out of the line and shows them; acting on them
belongs to the runner below.

A phase is normally written whole — members listed and each member's
dependencies filled in — before anything reaches the board, which is what
makes it a thing you can read in a diff. For the card you decide belongs
after all there is **⟶ phase**, on `backlog/` and `to-do/` cards that are
not already in a phase and are not phase cards themselves. It opens a
sheet naming the phase cards in `to-do/` and what each already holds, and
picking one appends `- <n> — <title>` — the way a person writes it — to
the end of that phase's `## Cards`. Nothing else moves: the card stays in
its stage, because joining a phase is not a commitment to start it. The
append goes through the board's own write path, so it commits itself under
`BOARD_COMMIT_MOVES` and reaches the other boards; an addition that never
left one working tree is not an addition the phase would run. Only phases
in `to-do/` are offered — one in `in-progress/` is running, its members
being worked in the order the list had when it started — and with no phase
waiting there the action is absent rather than present and empty.

### A phase runs itself, on a branch of its own

Running a phase works its list into a single integration branch. Starting
one cuts `phase/<task-stem>` from the newest `origin/main` it can see — the
same rule and the same timeout a task branch is cut by — and gives it a
worktree beside the task worktrees. From there each member is branched
**from the phase's tip**, run headless exactly as **▸ start work** runs any
card, and merged back into the phase branch when its checks are green;
then the next one starts. At the end one PR, from the phase branch into
`main`, for a human.

That is why the branch exists. Members of a phase are related by
definition, so card two branched from `main` could not see card one's work
while card one sat unmerged in `review/` — it would conflict, or quietly
build the same thing twice. Gating on a merge into `main` would fix the
branch point and destroy the point, because `main` is merged by a person
and the phase would stall on every card. So the human gate moves from every
card to the phase boundary, and the promise survives intact: the board
merges into a branch it created, inside a scope you opened, and `main`
still waits for your click.

**The runner is a beat, not an agent.** Everything it decides is already
structured state — a card's stage, a PR's CI verdict, whether one branch is
contained in another — so an agent paid to poll would be the wrong tool at
the wrong price. It is a plain thread (`BOARD_PHASE_INTERVAL`, 30s), silent
when no phase is running.

**The beat is stateless.** Each pass recomputes which members are finished,
which is first unfinished and what that one needs; it holds no registry of
where a phase *is*. Two durable things carry the memory instead, and both
are things the board already writes: **git**, where a member is finished
when its branch is contained in the phase branch, and **the phase card**,
which grows a `## Phase log` section the runner adds one line to per
decision — a run started, a member started, a member merged, a halt. The
log is the record a person reads, and the only thing that can tell "this
member has run and it ended badly" from "the phase has not reached it yet";
without it a restarted board would relaunch a run that died. So a restart
resumes a phase by looking, and the same logic answers "what now?" whether
the last event was a launch, a merge or a crash.

**Advance on green.** A member is finished when its card reaches `review/`
and its checks are not against it. Green is read from the same PR poll the
board already runs: red halts the phase, running holds it, and a member
with no checks at all advances — a project without CI must not deadlock
every phase it runs. A member with no branch at all that is already in
`review/` or `done/` is simply finished; there is nothing to bring.

**Halt, never skip.** Five conditions stop a phase, each already a visible
state on the card: a member that exits `NOT READY`, a run that exits
non-zero, a clean exit that committed nothing, CI red, and a merge into the
phase branch that is not mechanical. A phase that stepped over a failed
card would build the rest on a foundation that never landed. The halt is
written into the log and then held — said once, not once a beat — and
nothing retries by itself. Running the phase again is a person's decision,
and it is what appends the line that clears the halt. A member whose
`**Depends on:**` names something unfinished is a *wait*, not a halt: the
phase idles until the dependency lands (merged, for a card inside the
phase; `done/`, for one outside).

**Merges are additive, always.** Nothing here rebases and nothing
force-pushes. `main` is merged into the phase branch on every beat, so a
phase that runs for hours does not drift into one enormous conflict at the
end; a conflict there halts the phase like any other, aborted cleanly, with
the colliding files named. When every member is in, the branch is pushed, a
PR into `main` is opened with the member list as its body, the `**PR:**`
line is written into the phase card and the card moves to `review/` — where
the existing apparatus applies unchanged: the CI chip, **◔ review PR**,
**⚑ copilot**, and drag-to-`done/` for **merge & clean up**.

**One board runs it.** State syncs; reactions don't, so the phase card's
**Assignee** is where "who runs it" is written down — the same claim that
gates starting work. A replica renders the phase and advances nothing.

Members run one at a time. Running independent members in parallel is a
separate card.

### Watching one run, and watching it stop

A phase card is a card, so the PR chip, CI, the review actions and the
merge-and-clean-up sheet all reach it by inheritance. Three things do not,
and they are the phase's own interface:

- **▸ run phase**, on an `in-progress/` phase card, in the slot **▸ start
  work** occupies on an ordinary one — arming and firing like every other
  launch, and reading **▸ take over** on a card someone else holds. Moving
  the card to `in-progress/` stays the commitment; this is the second half
  of it. **‖ hold** replaces it while the phase runs and means what it
  means everywhere else: the run stops and the member agent in flight
  stops with it, while the phase branch, every card already merged into it
  and every worktree are left exactly as they were. A hold is not an undo,
  and a held member's card keeps its own work — so running the phase again
  may halt on it, which is the honest reading of a run that ended without
  reaching `review/`.
- **A chip in the header while a phase runs**, beside the agents chip and
  present on the same terms as the sync chip — only when there is
  something to say. It breathes in `--accent` and names the phase, how far
  it has got and the card in flight: `⟶ Ship the site · 2/5 · on #33`.
  Progress is what has landed on the phase branch, which is a different
  fact from which card is running, so both are there. Two phases could in
  principle run on one board, and two phases get two chips — a chip that
  silently showed one of them would be worse than none.
- **The halt, in `--alarm`, holding.** `⟶ Ship the site · halted at #35 —
  it is not ready` stays in the header until the phase is run again or
  held, rather than scrolling away; a toast fires with it, because a halt
  is rare and actionable; and the ticker keeps the line. The member card
  wears its own failure independently — a phase you started and stopped
  watching is only trustworthy if its halt is impossible to miss, so it is
  told three times at three altitudes, exactly as a dead run is.

Every advance is narrated in the ticker as it happens: the member that
came up green, the merge into the phase branch, the member starting next.
And the phase card's sheet lists its members in run order with each one's
stage — and, while a phase is in flight, the runner's own reading of each
(merged in, working, checking, stopped here) — so the card answers "where
is this up to" without a hunt across five columns.

### A phase's members leave the Board view

The Board is the work you are personally holding, in five columns that
fit, so a phase's members are **not drawn there**. Nothing is deleted,
moved or marked: a member keeps its stage, its file, its agent and its
actions, and the Board simply stops listing it. A column's number is
therefore the number of cards you can see in it — true by construction
rather than true-with-a-footnote — with `+2 in phases` beside it saying
where the rest went, and only where members are actually hidden. A board
with no phase card on it renders exactly what it always did.

Membership is the only thing that hides a card, so *removing* membership
is the un-hiding, with no sweep and no second rule: archive the phase
card, drop a number from its `## Cards` list, or let the phase reach
`done/` — it holds nothing once it is over — and its former members are
back in the columns they are genuinely in. A membership that did not
resolve hides nothing either: a card wearing `phase drift` stays on the
board, because an authoring mistake must not make work vanish.

That leaves the phase card standing for all of it, so it carries the
summary it owes: a `⟶ 1 of 2 merged` chip in the footer row that opens
its own sheet (the member list, in run order, with each one's stage), the
member in flight on its activity line, and a run that halted worn in
`--alarm` like any other stopped work. Only the Board hides. Sessions and
Focus are about runs, not stages, and a phase member's agent is an agent
like any other — it is counted by the header's live chip and by the tab
title wherever it is working.

Filtering the board to a phase's cards, and a Focus view for a phase, are
separate cards.

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

The board parses the task numbers out of the line (prose preconditions are
left for the reader) and nothing enforces them yet: they inform whoever picks
the next card, and guard what a phase may start.

The rest of the file is freeform — description, research findings, approach,
open questions, whatever is relevant to the current stage.
