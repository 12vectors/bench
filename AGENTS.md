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
│   ├── archive/          ← Archived cards: out of the flow, never deleted
│   └── task-template.md  ← Yours: seeded once by install.py, never replaced
├── plans/                ← Claude Code plan files (via plansDirectory setting)
├── reference/            ← Supporting documents referenced by tasks
└── manager/
    ├── core/             ← The tool. Replaced WHOLESALE by update.sh — never
    │   │                    put anything project-specific here.
    │   ├── VERSION, board.py, config.py … httpd.py, board.html
    │   ├── prompts/      ← Default agent prompt templates
    │   ├── adapters/     ← Agent-vendor integrations (claude/ and
    │   │                    opencode/ ship; contract in README)
    │   ├── seed/         ← Tool-owned starter material (the task template)
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

That is the vendored layout, where the work state lives in the install.
`tasks/`, `plans/` and `reference/` are not shipped: `install.py` creates
them at the work root the layout resolves to — here, or `BOARD_WORK_ROOT`
in an external install, and never both (see "Where the work state is
created").

Three layers, one law: **core knows about tasks, worktrees, PRs and events —
it knows nothing about any particular app, agent vendor, or project.**
Drivers know apps, adapters know vendors, `local/` knows this project.
`tasks/` holds only state and works as a plain folder kanban even if
`manager/` is deleted; the board narrates hand-moves when it happens to run.

Module map for `manager/core/` (dependencies flow strictly left to right):

```
config → state / reports / templates → taskfiles → plans / profiles → reviews → verify → progress → acceptance → gates → doctor → events / github / drive / sync → delivery → agentsettings → settings → setupguide → agents → setuptasks → readiness → previews → phases → refine → watch / httpd → board.py
```

- `config.py` — paths, stages, settings, prompt/adapter/driver resolution
- `state.py` — shared registries, event log persistence, SSE fan-out
- `reports.py` — what the record keeps of an agent's closing report: one
  cap, one clip, for the task file and the PR body alike
- `templates.py` — the four launch prompt templates: what each may and
  must contain, which text a launch actually used (name, source, hash),
  and the contract check asked before a save and before every launch
- `taskfiles.py` — reading and moving task files; the only code touching tasks/
- `plans.py` — one card's execution plan: which application branch its work
  starts from, which branch its PR targets, what selected each, and the
  preflight that validates all of it before a branch, a worktree or an
  agent exists
- `profiles.py` — the other half of the same decision: what a run may do
  in that checkout and which files it is guaranteed to have read. One
  profile per launch intent, the approved context it pins read-only, and
  the manifest recording what each run actually rode
- `reviews.py` — the checkout a PR review runs in: the pull request's
  repository, base and head resolved before anything is created, a
  detached worktree of that exact head owned by the run, and whether the
  review still speaks for the head the pull request proposes
- `verify.py` — the named checks bench runs itself, on a run's request:
  the argument list built here rather than composed by a model, the
  environment the check is given, and the evidence it leaves — the
  runner's own exit status, bound to the source it ran against
- `progress.py` — what a run *says* about its own work: the one
  run-scoped channel it reports through, each report validated whole at
  ingestion, ordered, de-duplicated and coalesced, closed by the run's
  observed ending, and kept apart from anything bench verified
- `acceptance.py` — what a card's requirements have been *judged*
  against: each criterion's state computed from the card's own records,
  bench's evidence and the runs' claims; review rounds and the findings
  carried through fix and recheck; and the three explicit, attributed
  writes (track, decide, a finding event) — never a tick inferred from a
  lane, an exit or a green suite
- `gates.py` — may this merge happen? The operator's requirement per
  guarded mutation, read against evidence bound to the exact commit about
  to move; bench's own review verdict kept apart from GitHub's formal
  state; and an override that records who proceeded over what, and never
  forges a pass
- `doctor.py` — what this board would grant, run and refuse, asked
  without paying for a model: the same resolver a launch rides, read at
  four honest distances, with every diagnostic's remedy and a coverage
  table that says which distances were *not* taken
- `events.py` — ingests NORMALIZED events (the adapter contract), session registry
- `github.py` — PR opening, Copilot requests, review/CI polling
- `drive.py` — runs the project driver, tracks the one live drive
- `sync.py` — origin/main as the shared board: push on move, pull on a beat
- `delivery.py` — one delivery status per card, computed server-side
  from the authorities above: what is happening, the one next action and
  who holds it, and the merge requirements that apply at the current
  commit. It owns no policy and no enforcement, and a read of it fetches,
  launches and writes nothing (see "Delivery status: one answer per
  card")
- `agentsettings.py` — Settings → Agents: the harness and model each
  purpose rides and the local prompt overrides, read from the one
  launch-time resolver and written only as the agent keys of
  `local/.env` and four named templates, with conflict detection,
  backup, rollback and a sanitized history
- `settings.py` — how this board is configured, assembled from the
  modules above and nowhere else: it names the states so nothing reads as
  a pass, compares two resolutions so a change can be previewed, and
  writes one file. No second parser, by construction
- `setupguide.py` — the project setup guide: six areas read from the
  authorities above, eight status words and no percentage, and the only
  two writes it owns — a person's decision about an area and a proposed
  setup card — held to a revision and a request identity
- `agents.py` — headless work/review jobs, launched through the adapter
- `setuptasks.py` — setup cards from discovered gaps: what inspecting
  the application's files found absent, previewed exactly, created
  idempotently by stable gap identity, and started only through the
  normal start-work path (so it sits right of `agents`, which it calls)
- `readiness.py` — verification readiness: candidate checks discovered
  from scripts, CI and docs without running them, registration and
  merge-rule drafts handed to Settings' own validate, preview and apply,
  and a person's review — bound to the check's identity — as the only
  thing that makes a check *ready*. The bootstrap condition it reports
  changes no gate
- `previews.py` — application previews: discovery that proposes and runs
  nothing, a machine-local configuration refused or called out where it
  is read, and Prepare, Start, Check readiness and Stop as separate
  explicit acts. A started preview is a drive (`drive.py` takes its launch
  spec), bound to its source, ready only when its check answers, and
  stopped and re-adopted only when it is provably bench's own group
- `phases.py` — a phase run: its own branch, its cards merged into it one
  at a time. A beat, not an agent; it holds no registry of where a phase is
- `refine.py` — refining a card on the board: comments, the one
  refinement job a person asks for, its validated proposal, and the
  explicit Apply or Discard. The brief and the decisions live in the
  card; this holds only machine-local job state
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

Two further things reach `run`, and both are *additions*: `AGENT_PROFILE`,
the resolved execution profile for the launch (named command grants,
denied capabilities, the working-directory policy), and `AGENT_CONTEXT`,
the read-only inputs bench pinned for it — paths and hashes, never
content. `AGENT_COMMANDS` is still set to exactly what it always was, so
an adapter override that has never heard of profiles is handed the
environment it was handed before and keeps working; one that does read the
profile reads it as a **narrowing**, which is why passing it cannot
broaden what a legacy board grants. Beside its `run`, an adapter declares
what it can genuinely enforce in a `capabilities.json`, so a profile may
refuse rather than claim a guarantee the vendor cannot make — see
"Execution profiles and approved context".

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
One drive at a time; **park** takes it down. A configured preview from the
setup guide rides the same lifecycle, adding source binding, a readiness
check and owned-only cleanup (see "Application previews: configure,
prepare, start, check, stop").

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
board is too far to drag: a **⌸ archive** tool in the card's **⋯** menu
and in its detail view, wearing the tray's own glyph, arming on the first
click and firing on the second. It is there on exactly the cards the tray accepts — a working
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
tasks, plans, reference documents, your task template, driver, prompt
overrides, `.env` and state all survive, in either layout. The tool-owned
seed under `manager/core/seed/` is replaced with the rest of core; your
template at the work root is not. No release
published → it says so and changes nothing; developers working on bench
itself update their clone with git instead. Then re-run `install.py`
(idempotent re-wire) and restart the board — which, after an update from
a release that still unpacked a skeleton beside the tool, is also what
tidies that skeleton away in an external install (see "Where the work
state is created"). Releases are cut from the
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

It also lays down the work state, because the release no longer carries
one: `tasks/` with its five stages and `archive/`, `plans/` and
`reference/`, each new directory holding a `.gitkeep` so it survives a
commit, and `tasks/task-template.md` copied from the tool-owned seed. In
a vendored install that is inside `.task-manager/`, exactly where it
always was. Whatever already exists is left as it is, so a second run
creates nothing.

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

Empty is honest, but honest and invisible is a board that lets an agent
discover the problem on your behalf, slowly — so the board says it out
loud. With `BOARD_AGENT_COMMANDS` holding nothing (unset, whitespace, or
a lone comma, which is how the adapters read it too) the header carries a
quiet `no agent commands` chip naming the setting and the file that holds
it, `manager/local/.env`, and every launch of the two intents that would
have run those commands — **▸ start work** and **↻ act on PR** — appends
the same sentence to its line in the ticker. It is a configuration fact,
not an alarm: it wears the settled register rather than `--alarm`, it
appears only while the setting is empty, and it never refuses a launch.
An agent that only edits files is still useful, and bench does not
decline work because a project is unconfigured.

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

## Running outside the application

Everything above assumes bench lives in the repository it manages. It
does not have to. An existing application can be worked with bench
without the tool, its task files or its bookkeeping ever appearing in
that application's history — the tool in one directory, the tickets in a
git repository of their own, the application untouched except by the code
the agents write.

```text
workspace/                       # a grouping directory, not a repo
├── app/        BOARD_APP_ROOT   # the application + its own origin
├── bench/                       # this install: tool and configuration
├── work/       BOARD_WORK_ROOT  # tasks/, plans/, reference/ — its own repo
└── worktrees/  BOARD_WORKTREES  # the application's task branches
```

```bash
python3 bench/install.py --app /ws/app --work /ws/work
```

That writes the two roots into `manager/local/.env`, builds the work
state's skeleton, and wires the agent adapter **without putting anything
into the application**. Nothing else changes: with neither setting, every
path resolves exactly as it always did, so a vendored installation needs
no migration and reads no differently.

The installer takes six flags and no others. Four shape an external
install: `--app` and `--work` name the application and the work state,
both as absolute paths — bench resolves nothing against a working
directory it cannot see; `--worktrees` moves the task branches'
checkouts off the default beside the application; and `--app-hooks` opts
into writing the application's own local settings file, refusing if git
tracks it (see "Hooks without a tracked file"). Two apply to any
install: `--dry-run` reports what the rest would do and writes nothing,
and `--setup` asks the first-run questions again.

`bootstrap.py`, beside `install.py`, is the guided way to the same
result, for a person or their coding agent: `inspect` discovers and
writes nothing, `plan` previews every operation, and `apply` makes them
only once approved, running `install.py` for the installing and ending
with a versioned, sanitized handoff that names the board's URL. It
initializes the work state's repository only with consent, and creating
a (private) remote, the first push and sync are separate explicit
choices. The README's "Set up with your coding agent" is its whole
reference.

Each of the three roots is a plain setting underneath —
`BOARD_APP_ROOT`, `BOARD_WORK_ROOT` and `BOARD_WORKTREES` in
`manager/local/.env`, every one of them documented in
[`core/.env.example`](manager/core/.env.example). So an install already
made is retargeted by editing that file and re-running `install.py`,
which is idempotent, rather than by installing from scratch.

### Four roots, named separately

They were one directory tree with one git identity, derived from where
`manager/` sat on disk. Now each is its own answer:

| Root | What it holds | Default |
| --- | --- | --- |
| the tool | `manager/core`, `manager/local`, prompts, adapters, runtime state | where `install.py` sits |
| **the application** | code, branches, worktrees, PRs | the git repository the install sits in |
| **the work state** | `tasks/`, `plans/`, `reference/` | the install itself |
| the worktrees | the application's task branches | `.worktrees` inside it, or `worktrees` beside an external one |

Runtime state stays with the tool in every layout — agent logs, drive
logs, command logs, session timelines, the generated hook settings. None
of it is shared, none of it is committed, and none of it belongs to
either repository.

### Two git identities

| Operation | Repository |
| --- | --- |
| branch, worktree, commit code, push, open/review/merge a code PR | the application |
| create/edit/move/archive a ticket, record an outcome, commit and sync board state | the work state |

The work state's repository is *derived*, not configured: it is the
repository holding the work root. Vendored, that is the application's own
— `tasks/` genuinely lives inside it, and pretending otherwise would
change behaviour nobody asked to change. Externally it is a repository of
its own, and a work root in **no** repository is a work root bench will
not commit to rather than a reason to reach for the application's
history.

"Externally" means either root is set, not only the work root. With
`BOARD_APP_ROOT` alone the work state keeps its default — the tool
installation — and its repository is whichever one holds that install:
the tool's own checkout, a repository it was dropped into, or none at
all. None is a working local-only board; with `BOARD_COMMIT_MOVES` or
`BOARD_SYNC` on it is refused, naming the install as the work root and
`BOARD_WORK_ROOT` as the way to give the tickets a repository of their
own. An install sitting inside the application it names is refused as a
work root inside the application, which is what it is. The application's
repository is never the answer.

Each identity resolves its remote and its base branch independently:
`BOARD_GIT_REMOTE` and `BOARD_APP_BASE` for the code,
`BOARD_STATE_GIT_REMOTE` and `BOARD_STATE_BASE` for the board. So a card
moving columns cannot produce a bookkeeping commit on the application's
`main`, and a code PR cannot target the work-state remote. `gh` is told
which repository it is acting on rather than inferring it from a checkout
that may have several remotes.

One consequence is worth saying out loud, because it used to be wrong:
**`BOARD_SYNC` is about the board's state and nothing else.** It decided
how the *application* was merged too — fine while the two were one
repository, meaningless once they are not. `BOARD_APP_MERGE` is that
decision now, and it defaults to the answer the old behaviour gave
(see `core/.env.example`). When origin makes the merge and no sync beat
watches the application, the board fetches and fast-forwards the
application's base itself, because otherwise nothing would.

### Tickets local only, versioned, or shared

The work state is a repository bench reads and writes but does not
insist on. Three arrangements, and the board behaves differently in each
because each is a different promise:

- **No repository at all.** A work root `git init` has never been run in
  is still a working board: cards move, agents run, branches are cut,
  PRs open. What it cannot do is commit, so `BOARD_COMMIT_MOVES` or
  `BOARD_SYNC` turned on there is refused by name with `git init` as the
  way forward. Tickets are yours alone, and nothing is quietly committed
  to the application instead.
- **A repository of its own, pushed by hand.** With
  `BOARD_COMMIT_MOVES` on, every write the board makes to a task file —
  the move, the claim, the `**PR:**` line, an agent's closing report —
  lands as a `board: …` commit in the *work state's* repository and
  stays there until you push it.
- **Shared through a remote.** `BOARD_SYNC` adds the event-driven push
  and the pull beat on top, through `BOARD_STATE_GIT_REMOTE` and
  `BOARD_STATE_BASE` — the work state's own answers, resolved
  independently of `BOARD_GIT_REMOTE` and `BOARD_APP_BASE`. Everything
  in "Syncing boards" applies as written; the only difference is which
  repository it converges.

None of the three is a push of the application. What pushes a branch and
opens a PR is a card entering `review/`, in application git, on the
board whose user moved it and nowhere else: a replica renders that move
and triggers nothing, so no PR is opened twice, and **↑ open PR** is the
action that asks for one after the fact. A board with no remote or no
`gh` cannot open it at all — the plan reads `local-only` and names the
missing capability, and the run goes ahead anyway. Merging into the
application's base stays a person's click under either value of
`BOARD_APP_MERGE`. So "the board committed" and "the branch was pushed"
are two events in two repositories, and the split exists so that neither
can be read as the other.

**What stays out of shared ticket history.** The work state holds what a
teammate should read: the tickets, the implementation plans in `plans/`,
`reference/`. Everything machine-local stays with the tool and out of
it — agent logs, drive and command logs, session timelines, the
generated hook settings, and the recorded execution plans under
`manager/local/state/plans/`, which hold this machine's worktree paths.
The execution manifests, the verification evidence, the review
checkouts' ownership records and the gate records — the Bench review
verdicts and the overrides (`state/manifests/`, `state/evidence/`,
`state/reviews/`, `state/gates/`) — are machine-local for the same reason
and by the same construction.
`manager/local/.env` is gitignored and belongs there too: it names the
roots on *this* disk and the binaries on this PATH. An absolute path out
of somebody's home directory, a credential, or a log that means nothing
on the machine that did not write it has no business in a repository
other people pull — which is why none of it is written anywhere a pull
could carry it.

### A card's branch belongs to the application

Execution plans and external workspaces answer different halves of one
question, so they compose rather than overlap: the plan decides *which
branch* a card's work starts from and targets, and the layout decides
*which repository* that branch is in. It is always the application's.

- **The default needs no metadata.** A card with no `**Base branch:**`,
  no `**PR base:**` and no phase resolves to `BOARD_APP_BASE` — `main`
  unless the operator changed it. That is as true externally as it ever
  was vendored, and it is the whole of "no new metadata keeps
  established behaviour".
- **The optional headers name application branches.** `**Base branch:**`
  selects the branch the work is cut from and `**PR base:**` the branch
  its PR targets, defaulting to the first. Neither has anything to say
  about the work state: a ticket's own history is not a branch a card can
  be based on.
- **Preflight refuses rather than falls back.** Everything is resolved
  and validated in application git before a branch, a worktree, a claim
  or an agent exists, and a named base that cannot be reached is refused
  instead of being swapped for the default. Under an external layout that
  matters more, not less — the fallback would be a different repository.
- **The recorded plan outranks the setting.** The validated plan is
  written with the run, and everything afterwards reads it, so changing
  a shared operator default retargets the next launch and never one
  already under way. The record is machine-local for the reason above;
  the portable half — branch names and a commit — travels through the
  ticker, the agent's brief and the PR body.
- **Prose chooses nothing.** `**Depends on:**` guards whether a card may
  run. It has never been an instruction to stack a branch or merge a
  prerequisite, and a plan always names the structured input that
  selected it, so a requirement that exists only in prose is visible as
  exactly that.

The rest is not repeated here. **"Execution plans"** is the reference for
it — a section of its own, listed beside this one, holding `plan drift`,
**⌖ replan** and preflight's whole list of refusals — and nothing in it
changes because the tickets moved out of the checkout. The two features
meet at one question, the one above: which repository the branch is in.

### Validation fails closed

Every fallback available to a broken external layout leads to the wrong
repository, so there are none. `config.problems()` is the single
authority; the board prints its answers and refuses to serve, `start.sh`
refuses before it touches a port, and `install.py` refuses before it
writes a line. It rejects a relative or missing root, an application that
is not the top level of a git checkout, the two roots naming one
directory or containing one another (including through a symlink, which
git resolves where paths do not), bench's own repository as the
application, a worktrees directory inside the work state, and
`BOARD_COMMIT_MOVES` or `BOARD_SYNC` turned on with no work-state
repository to commit to — naming `git init` rather than quietly
committing somewhere else.

### Where the work state is created

A release is unpacked before anything knows which layout it is going
into. So it carries no work state at all — no `tasks/`, no `plans/`, no
`reference/` — because whatever it carried would sit under the tool even
when the work root is somewhere else, looking like a second active board
beside the one that counts. `install.py` creates the work state instead,
at the **one authoritative work root** the layout resolves to:

- **Vendored**, or `BOARD_APP_ROOT` alone: the install itself, as it
  always was.
- **`BOARD_WORK_ROOT` set**: that directory, and nowhere else. The tool
  root is left with no `tasks/`, `plans/` or `reference/` of its own.

The layout is read the way the board reads it — the persisted
`manager/local/.env` and the environment, not only this run's flags — so
`install.py` with no arguments initializes the same place the flagged
run did. It creates only what is missing: the stage directories,
`archive/`, `plans/` and `reference/`, each with a `.gitkeep`, and the
task template. Every path is checked before the first write, and one that
a link would carry out of the work root refuses the whole step with
nothing written — in a vendored install as much as an external one.

**Who owns what.** Two copies of the task template, with two owners:

| Path | Owner | What updates do |
| --- | --- | --- |
| `manager/core/seed/task-template.md` | the tool | replaced with the rest of core |
| `<work root>/tasks/task-template.md` | you | never touched |

The installer copies the seed into place only when the work root has no
template. One that exists — edited or not, a link included — is yours
and is never overwritten, so an improved seed reaches a board that has
none and nobody else's. Everything else under the work root is yours the
same way: cards, plans and reference documents are created by people and
the board, never by an update.

**What an older release left beside the tool.** Releases before this one
did unpack `tasks/`, `plans/` and `reference/` under the tool, and an
install that has been vendored may have a real board there. So in an
external layout the installer looks at each of those three, one whole
directory at a time, and removes it **only** when it is demonstrably the
untouched skeleton a release unpacked: the expected directories, empty
`.gitkeep` files and a task template byte-identical to one a release
shipped — none of it tracked by git, none of it a link. Anything else —
a card, a plan, an edited template, a tracked file, a link, a file bench
cannot account for — keeps the *whole* directory exactly as it is, and
the installer names the authoritative work root, what it kept, and why.
Nothing is moved, merged or read from it; carrying old cards over is the
owner's decision, made by hand.

**Changing layout.** Vendored to external, external back to vendored, or
one external work root for another: the newly configured root is
initialized where that is safe — missing pieces only, nothing existing
overwritten — and the previous root is left exactly as it was, with no
import and no clean-up beyond the pristine skeleton above. Once a layout
has been external, the installer remembers the work root it last
initialized (`manager/local/state/work-root`, machine-local) and says so
when it changes, naming both. Data in two roots is never reconciled by
guessing: the one the settings name is the board, and the other is
reported, not touched.

### Hooks without a tracked file

Headless launches were always external-safe: the claude adapter builds
its whole settings document per launch and passes it with `--settings`,
so hooks reach an agent without anything existing in the application.
What `wire` does is give the operator's *own* sessions the same
visibility, and externally it writes that document under the tool's local
state instead of into the application, printing the
`claude --settings <path>` to use it with. `--app-hooks` is the opt-in
convenience that also writes `.claude/settings.local.json` — the
designated local override — and it **refuses if git tracks that path**,
so no bench hook configuration can become a tracked file in the
application even by accident. Unrelated hooks and settings keys are
preserved exactly.

An adapter that cannot do this says so. opencode discovers plugins under
the project it runs in, so there is nowhere outside the application to
put its event bridge; external wiring is refused with both ways forward
named, rather than quietly writing into the application or reporting a
success that would render no live sessions.

### What the record says about where things are

A PR body named `.task-manager/tasks/review/` whatever the truth was. It
now names the card's real location — a path relative to the application
when the tickets genuinely live there, and otherwise that they are kept
outside the repository, on the board. Clipped reports name their log
relative to the tool install, never by an absolute path that means
nothing on anyone else's machine. And the prompts tell a run where its
workflow brief actually is: the application's own `AGENTS.md` when it has
one, plus any brief that lives outside the checkout, by absolute path —
so an agent is told where to read the rules without a bench file being
added to the application for it to find.

### Nothing uncommitted is thrown away

Worth its own line because it was the one unrecoverable thing here. A run
that edits files and never commits leaves work on disk, and an unchanged
HEAD said "empty" about exactly that case — so the worktree was forced
out from under it. Now uncommitted changes and untracked files both keep
the worktree and its branch, the removal runs without `--force`, and a
git that refuses anyway is reported rather than assumed to have worked.
The same rule covers merge-and-clean-up, which already applied it to a
phase's members and now applies it to the card's own workspace.

And a worktree is identified by its repository, not by its name:
`<worktrees>/<stem>` is a path two things can want, so an agent, a
driver, a local command or a phase refuses a directory that is a checkout
of something else instead of acting on the wrong code.

### Lifecycle

`start.sh` and `stop.sh` identify "our board" by the triple it serves —
tasks root, application, tool — not by the tasks root alone. Two
operators sharing one work state while managing different applications,
or a preview install beside an operator one, must not reopen or stop each
other's board. Updates replace `manager/core/` and the top-level scripts
under the tool and reach nothing else, so tickets, configuration and the
roots themselves survive by construction; the first-boot clean, which
exists to clear the distribution's own cards out of a vendored install,
never runs externally — an external work root is a board of its own whose
cards are nobody's residue.

### An operator install, and the bench under test

Bench does not manage itself. `BOARD_APP_ROOT` naming the git repository
the tool itself sits in is one of the refusals above, and it is the
refusal that shapes how bench is developed with bench: an application
that is *a* bench checkout is fine, the one serving the board is not.

So developing bench this way means two of them, and the separation is
the point:

- **The operator** — the install you actually run the board from. It
  serves the cards, launches the agents and does not change under you
  while a run is in flight.
- **The candidate** — the bench checkout named as `BOARD_APP_ROOT`, which
  is where the cards' work happens. Agents branch it, commit in it and
  open its PRs like any other application.

A run that breaks the candidate therefore breaks nothing you are standing
on, and the same is true of a card that rewrites `manager/core/`.
Promotion is a deliberate, separate act and nothing does it by itself:
bring the tested code into the operator install the way that install is
maintained — `update.sh` for one unpacked from a release, git for a clone
— then `install.py` to re-wire and a restart to pick it up. Until then
the board runs the old code on purpose, and the card that changed it is
a card in `review/` like any other.

The two can share one work state or keep separate ones, and either way
`start.sh` and `stop.sh` tell their boards apart by the triple above, so
a candidate board opened for a look does not reopen or stop the
operator's.

## Seeing the board

```bash
./.task-manager/start.sh                 # the usual way: install + port + board
python3 .task-manager/manager/board.py   # or run the server directly
python3 .task-manager/manager/board.py --doctor   # readiness, then exit
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

The board has five views (header switcher):

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
  can settle. A card's face is kept quiet (see "The card face and its
  tools" below).
- **Phases** — a swimlane each: where the members the Board stopped drawing
  actually live (see "The Phases view: a swimlane each"). The switcher
  itself carries how many phases are running and a mark when one has
  halted, so a halt is learned from whichever view you are on.
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
- **Focus** — a heads-up display for one session: the task it holds, what
  the run *says* it has done, is doing, has left and is blocked on —
  agent-reported, with its own clock beside the last tool activity and the
  process state (see "What a run says it is doing") — its live
  TodoWrite plan, the project's configured definition-of-done checks (a
  `checks` file in `manager/local/` overriding the shipped default in
  `core/`), what the run was actually permitted to do and given to read —
  read from the manifest frozen at its launch, which is a different fact
  from what the settings say now (see "Execution profiles and approved
  context") — what **bench itself** ran and found, which is a different
  fact again (see "Verification: bench runs the check") — whether the
  work may actually be merged, which is a fourth (see "Gates: nothing
  merges on a report") — whether a launch of this kind would even start
  on this board and what is not proved about that (see "Readiness:
  asking before paying") — and per-file diff stats from its worktree.
- **Settings** — how this board is configured, what has actually been
  verified, and what to configure next: the checks, the merge rules, the
  review configuration, the overrides, the intended agent access against
  the isolation actually enforced, the diagnostic coverage, the
  installation and every setting's provenance — each value in words with
  a state word beside it, so "no gate configured" can never read as a
  passed gate. It is also the one place a policy is *edited*, in four
  separate steps of which only the last writes (see "Settings: reading the
  configuration, and changing it safely"). The switcher carries a mark
  when this board's execution policy refuses something, for the reason
  the Phases mark exists: the thing it refuses is the next launch.

### The card face and its tools

A card shows what you need to choose the next action, and the column says
the rest. The face has one ID (`#76` in the top row; a title that repeats
the card's own number is drawn without it), the title, the owner, `HIGH`
when it is, and at most one status pill — the card's **delivery status**,
the one answer the server computes for it (see "Delivery status: one
answer per card"). The page works out no status of its own.

**The face shows active work and actionable current problems.** A card
wears its delivery status only when it changes what you do next: an
agent or check running, a failed run, a check that failed or could not
run on the current commit, changes requested, a commit that cannot be
assessed, merge rules that do not resolve, an override in force, not
published, a merge in flight, and — in `review/` — the next delivery
step (`Checks needed`, `Review needed`, `Ready to merge`, `Merged on
GitHub`, `Waiting for phase`). Requirements that simply lie in the
future (a check that has not run on an in-progress card, a review not
yet due) are listed under **Merge requirements** in the card's tools and
detail view, in the neutral register, never as an alarm. Nothing is
dropped: every requirement is still listed, every one is still enforced
at the merge, and a genuine failure is never moved off the face. A card
sitting in its column as expected — not started, queued, ready to start,
in progress, an ungated review, delivered — wears no pill: those are the
column's own words. The page adds two quiet readings the server does not
make, and only when the delivery status has nothing to say on the face:
a phase card nobody has started (or one held), and `idle` (in progress,
nobody on it, nothing changed for a day).

The chips follow the same rule. Active work and current problems stay on
the face: red CI, conflicts, a PR open against the wrong base, changes
requested by Copilot, a policy that does not parse, a running drive or
command. So do phase navigation and a non-default branch plan. On review
cards the face carries at most one merge-requirements chip, `⚖ 2 needed`
or `⚖ ✕` (a current-commit problem) — its count is the required
obligations only — and an override in force wears `⚖ overridden` in the
alarm in every stage. The `◫` evidence chip joins the face only beside a
current-commit check that failed, could not run or is running; a stale
or historical record never puts it there. Everything else — the profile,
a plan the phase already explains, the evidence, the pinned review
record, requirements met or still ahead, CI that passed, the PR link,
the drive, the `$` commands and archive — is in the card's **⋯** tools.
It is one labelled button (`Tools for #76`) that opens in place and
closes on Escape, giving focus back. Nothing is dropped: the tools list
every chip the card has, built and wired by the same code as the face.

Two counts may join the tools, and only where there is something to
count: `☑ 6/9 accepted` once a card's acceptance is tracked, and
`◇ 2 open findings` (or `1 fix to recheck`) while review findings are
outstanding. Outstanding findings, and criteria a person recorded as
needing work or a recheck, are current problems and are on the face; an
acceptance count on its own is not, because confirming criteria is not
a default ceremony (see "Acceptance and review findings"). Each opens its
section of the detail view. The word is *accepted*, never *done*: the
ratio is how many criteria a person has accepted on record, not how
complete the work is.

The card's detail view leads with what the card is and what happens next:
its number, title and delivery status stay in view while you read (the
head is sticky, and **Expand** widens the view for long briefs and
reports), and five sections follow as tabs — Tab reaches the row, the
arrow keys, Home and End move along it.

- **Overview** — the delivery status: its word as a heading, its
  sentence (announced as an alert only in the alarm tone), the one
  primary action and who holds it, and every other legitimate action
  under **More actions** — a guide, not a wizard: nothing in the list is
  disabled because the primary comes first, and an action whose handler
  belongs to a later card is drawn disabled with that reason. Then
  **Merge requirements** — `1 of 2 merge requirements met for <commit>`,
  one row per required obligation with its state in words and, for a
  problem, its remedy — with **Optional checks and earlier runs** and
  **Technical details** (the commit, the merge rule, the completion, the
  PR as of when it was observed) folded underneath. Then the stage, the
  latest run's progress as #73 reports it (agent-reported with its age
  while the run lives; *history, not current* once it has ended;
  *reporting unavailable* where it was never given a channel), the
  acceptance count, the findings, and the last review round explained
  against its revision and what has happened since. Then the **Signals**
  block — Bench checks (`Required: 1 of 1 passed on <commit>. Optional:
  2 (see ◫).`), external CI, review and the merge requirements as four
  separate rows, each in its own words — and every tool and action the
  card has, with the branch plan folded underneath. Green CI is labelled
  as not a Bench check, and a board with no gate says that nothing is
  required, which is not a pass.
- **Brief** — the requirement alone (the head's fields and the brief, no
  history), then #65's discussion and its refinement controls.
- **Acceptance** — the criteria and their results; see "Acceptance and
  review findings".
- **Reviews** — open findings first, each with its fix and resolution
  chain, then resolved findings and earlier rounds folded away.
- **Activity** — the dated record, newest first: progress updates
  (labelled as the run's), work reports and reviews (each folded, whole),
  comments and decisions, and acceptance and finding records. Raw reads,
  edits and commands stay in the Sessions view.

A filed report — a review round, a work report, a PR update, a
relevance review — is drawn as the document it is: through the same
Markdown renderer as the brief, with proportional prose, headings nested
under the section's own, real lists, tables and code blocks that scroll
in place, and monospace kept for code. Rendering is presentation only.
The text stays the card's, byte for byte, and its **Original Markdown**
is one fold away to read or copy. The round's reviewer, revision, date
and verdict stay in its summary line. Nothing in the prose is read as a
verdict, a finding or an acceptance. Report text is untrusted: raw HTML
arrives as text, a link is followed only to the web, mail, an anchor or
a relative path, and an image is drawn as a link to it, never fetched.

A live update keeps your place: each section keeps its own scroll
position, open folds and unsent text survive a redraw, and a record
arriving above what you are reading moves the page rather than you.
Cards are reachable by keyboard. Tab lands on a card, Enter or Space
opens its detail view, focusing a card shows the same actions as hovering
it, and closing the detail view returns focus to the card. Board and
Phases draw the same card.

The header carries one more chip, and unlike the sync and phase chips it
is always there: **⚕ configuration** opens the Configuration check. Those two
are states that come and go; this is the door to a diagnosis, and a door
that appeared only once something was wrong would be a door nobody
learned about. Opening it reads and starts nothing.

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
the board. The four launch templates are edited, previewed and checked
against their contract in **Settings → Agents**, and an override that
breaks it refuses the launch rather than reaching the agent (see "Agents:
harness, model and prompt per purpose").

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

It also refuses a **phase card**, and the refusal names **▸ run phase** —
a phase card's body is a list of other cards, so a work agent handed one
implements the table of contents. The board's own page offers one action
or the other and never both, but a UI layer can be stale or bypassed, so
the rule lives where every other launch refusal lives: with the stage
check, ahead of the claim and well ahead of the worktree, costing nothing
and leaving nothing to clean up. Which headless kinds a phase card may
host is decided rather than left to omission: **▸ start work** and **↻ act
on PR** refuse it (both are work agents, and a phase's work belongs to its
members' cards), while the read-only pair — **◔ still true?** and **◔
review PR**, the latter told the phase's own branch — are allowed. The
guard is about *starting*: a card that gains `**Type:** Phase` while an
ordinary run is in flight is left alone, and that run ends as it would
have.

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

### What a run says it is doing

A stream of reads, edits and commands proves a run is *active*. It does
not say what it has accomplished, what it is attempting now, what is left
or what it is stuck on — and only the run knows that. So a work, act-on-PR
or review run is given one narrow way to say it, and the board keeps what
it says apart from everything the board observed itself.

**The channel.** When the adapter declares `progress-report` in its
`capabilities.json` (both shipped adapters do), a launch is handed the
path of `manager/core/bench-progress` (`AGENT_PROGRESS`) and a token that
belongs to that run alone (`BOARD_PROGRESS_TOKEN`). The adapter renders
that one executable as an allow-rule. Nothing else comes with it: no
shell, no write to the task file, no lifecycle action, no network beyond
this board's loopback port, and no model call of the board's own. The
prompt asks for a short approach at the start, then an update at each
milestone, approach change, blocker, and the start and end of significant
verification — what changed, what is happening now, what remains — rather
than one per read or edit. An update never ends the run and is not its
closing report; `NOT READY`, the closing report and the one-turn rules are
unchanged, and a reporting failure tells the run to carry on working.

**What a report may hold, checked where it arrives.** A stage, what it is
doing now, milestones done, what remains, a blocker, a short narrative,
and references to commits or to bench's own evidence records — each
bounded, control and bidirectional characters refused, a line that reads
as a closing-report marker refused, credential-shaped text masked, and no
URL or path accepted as a reference. A report naming another run, carrying
the wrong token, or arriving after its run ended is refused; a refusal
against a real run is counted on that run's channel health, so a spoof or
a broken shim is visible rather than silent. Every refusal is counted,
but the page is told about refusals at most once every few seconds per
run, with one trailing notice — so a burst of bad reports cannot make
every open page reload on each request. Each update is a whole
statement: a field it leaves out is *not reported*, never carried over
and never inferred. The token is a run-scoped capability, not an
operating-system boundary — processes of the same user can read each
other's environment.

**Ordering, replay and noise.** Every report has an id, kept across the
shim's retries, so a repeated request is answered as a duplicate. Every
report has the time the run made it, so a late arrival is filed into the
history and never displaces a newer current update. Updates closer
together than `BOARD_PROGRESS_MIN_GAP` are coalesced into the previous
history entry — a blocker never is — and a run is capped at 200 accepted
updates; the history itself keeps the newest 50. The **milestone
checklist** is kept on the record apart from that history, so neither
coalescing nor the history cap takes a reported milestone off it: each
milestone is listed once, dated by the earliest report that named it, the
earliest 100 are kept and any beyond are counted rather than dropped
silently, and it survives a restart. A run quiet for `BOARD_PROGRESS_REMIND` seconds (600 by default,
0 turns it off) is reminded once, at its next tool boundary, by the event
bridge's reply: context on a tool result it was going to read anyway,
never an interruption.

**Endings supersede.** When the board sees the run end, the record is
closed with what it observed — finished, failed, declined, held — and the
last update becomes dated history, drawn as *last said* rather than
*doing now*. A board that restarts closes every run it was tracking as
*unobserved*, because it will not see their endings. A new run on a card
never wears its predecessor's update. Records are machine-local, under
`manager/local/state/agent/progress/`, beside the run logs.

**On the board, three clocks and a trust label.** A card with a live run
carries the run's last update above its last tool line, marked
`agent-reported` with its age, and a blocker in words. **Focus** leads
with a **Progress** section: stage, done (the milestone checklist), doing
now, remaining, blocked and the narrative; then **last progress update**,
**last tool activity** and **process** as three separate facts; the
channel's health; and the timeline, with bench's own verification events
interleaved and labelled `bench`. Silence is said as silence — *No recent
progress update; tool activity continues*, *No progress update yet*,
*Reporting unavailable* — and never as "stuck" or "thinking". Nothing
computes a percentage. **Sessions** gains a **Progress updates** filter,
and the technical activity stays where it was, expandable as before.

**Agent-reported is not verified.** "Tests passed" in an update is a
sentence. A reference to an evidence record is drawn from bench's own
record as it reads *now* — check, scope, commit, outcome and freshness —
so a failed or stale run cannot be promoted by a narrative or a clean
exit, and a missing record reads as missing.

**Fallback.** An adapter that does not declare `progress-report` — an
override written before it existed, say — is handed neither variable,
its prompt carries no reporting section, and the board says *reporting
unavailable* for its runs while showing their tool activity exactly as
before. The `refine` intent has no shell and never gets a channel.

**One record per raw event.** The timeline used to show some edits and
commands twice. The cause was two event bridges for one session: a run in
a worktree of a repository whose own `.claude/settings.json` wires the
bridge — bench developed with bench — received the hooks from that file
and again from the launch's `--settings`, and the vendor de-duplicates
identical hook commands only, which these were not. Now the launch names
its canonical bridge (`BOARD_EMIT`) and any other copy stands down; every
event carries a stable id (the hook phase and the vendor's tool-use id),
so a replayed copy is recorded once even across a restart; and once a
session delivers ids, an id-less copy from an older bridge is dropped.
Distinct repeated edits and test runs have distinct ids and are never
collapsed for having matching text, and a tool's start and finish stay
two events.

One limit remains, and it is a person's own session rather than a
headless run: with no `BOARD_EMIT` to name the canonical bridge, both
copies report, and the events that carry no vendor tool-use id —
session start and stop — get a fresh id per copy, so those two can still
appear twice. Tool events are de-duplicated there as everywhere else.

## Execution plans

Bench owns the git lifecycle. The coding agent implements inside the
checkout bench prepared and never chooses a different one — so *which*
checkout that is has to be decided once, written down, and read back by
everything downstream. A launch used to answer "where does this branch
from" and "what does its PR target" separately, at different moments and
from different code, which left no way for a card to say it belonged on a
branch other than the default and no way for the board to refuse when
that branch could not be reached. One recorded **execution plan** is the
answer to both.

A plan says, for one card: the application branch the work starts from and
the commit that resolved to, the branch the work lives on and where its
worktree goes, the branch its PR targets, which structured input selected
each of those, what this board can actually deliver, and what stops it if
anything.

### Where a plan comes from

Three structured inputs, in this order, and nothing else:

1. **Phase membership.** A member inherits its phase's branch and targets
   it — that is what a phase's branch is for. A member card that *also*
   names a base of its own is two instructions for one decision, so both
   cards wear `plan drift` and the launch refuses, rather than one source
   silently winning.
2. **The card's own headers** — `**Base branch:**` and `**PR base:**`. An
   explicit base selects the application branch to start from, and the PR
   target defaults to that same branch unless `**PR base:**` says
   otherwise. Both are validated where the task file is read: these values
   end up in an argument list, so anything that is not a branch name is
   refused by the parser rather than by the process that would have run
   it.
3. **The application default** — `BOARD_APP_BASE`, `main` unless the
   operator changed it. A card with neither header and no phase resolves
   exactly as it always did, which is the whole of "no new metadata keeps
   established behaviour".

A `**Depends on:**` list is deliberately *not* one of them. It guards
whether a card may run; it has never been an instruction to stack a branch
or to merge a prerequisite, and reading prose as one is how a run ends up
on code nobody chose. So the plan always names which structured input
selected it, and a requirement that exists only in prose is visible as
exactly that.

### Preflight: before any git or agent side effect

Everything above is resolved and validated **before** a branch is cut, a
worktree is made, a card is claimed or a coding agent is launched — and
again immediately before the committed start, since the claim is itself a
write. Preflight reads, fetches (bounded by `BOARD_FETCH_TIMEOUT`) and
answers; it creates nothing. So every refusal costs one read and leaves
the card precisely as it was: no branch, no worktree, no claim, and
nothing on the board claiming to be running.

What it refuses: a base no branch has, locally or on the remote; a PR
target that does not exist; headers that are not branch names; a conflict
with phase membership; a directory sitting where the worktree goes that
git does not know as a worktree of this repository; and a resumption whose
recorded plan disagrees with the card as it now stands. An explicitly
required base that cannot be resolved or fetched is **never** swapped for
the application default — falling back is the failure this exists to stop.

Local-only work stays available, and stays honest about itself. A board
with no remote, or without `gh`, cannot open the PR a card asked for, so
the plan reads `local-only` and says which capability is missing; the run
itself goes ahead, because an agent that only edits files is still useful.
What the board will not do is report full delivery readiness while the
capability that would deliver it is absent.

The plan is on the board before you launch: a `⌖` chip in the card's
footer row, beside `CI` and `PR ↗`, saying where the work starts and what
it targets (`⌖ task/56-external-workspaces`), with the structured input
that chose it on hover. It appears only on cards that have something to
say — a board of ordinary cards on the default has no such chip anywhere.
The chip opens the preflight itself, which lists the whole resolved plan
or the one blocking reason, and the drawer carries the same two facts.
Headers that do not add up wear `plan drift` in the same breath as
`status drift` and `phase drift`.

### One plan, for the whole of the work

The validated plan — including the commit the branch was actually cut
from and the PR target — is recorded with the run before work begins, in
`manager/local/state/plans/<stem>.json`. It is machine-local on purpose:
it holds this machine's worktree path. Shared ticket history gets the
portable half — branch names and a commit — through the ticker, the
agent's brief and the PR body, and never a path from somebody's disk.

Everything after the launch reads that record rather than resolving its
own answer: the automatic PR opened by the move into `review/`, the
explicit **↑ open PR**, **↻ act on PR**, the PR poll, and the merge at the
end. Three consequences follow, and they are the reason the record exists:

- **A global setting change does not retarget work already under way.**
  Change `BOARD_APP_BASE` between a launch and its review and the card
  still goes where it was planned to go. The next launch gets the new
  default; this one keeps its own.
- **A stacked card is not merged into the application default by
  accident.** `done/` has always meant merged into the application base,
  and a card merged into its prerequisite is not that — exactly as a
  phase member merged into its phase branch is not that. So **merge &
  clean up** validates the target and refuses a stacked card, in both
  modes and for that reason rather than for what the local merge happens
  to be able to do. The way through is the stack's own: land this card's
  PR into its prerequisite, and when the prerequisite itself merges the
  card's target is gone — the board says so, and a **⌖ replan** onto the
  base is the decision that makes the card finishable here.
- **A contradiction is reported, never resolved by guessing.** A PR
  already open against a branch the card does not target is neither
  duplicated nor retargeted: the card wears it, the ticker says it, and a
  person settles which reading is right. A target that has disappeared —
  a prerequisite merged and deleted — is the same shape of fact.

Resuming works the same way. An existing worktree or branch is validated
against the recorded plan, and nothing is reset, rebased or removed to
make it fit: uncommitted edits and untracked files are exactly what a
"repair" would cost. A card whose requirements changed under a running
branch needs the one deliberate action that retargets work, **⌖ replan**,
which records a new plan or refuses — and refuses the one thing it cannot
honestly do, claiming a branch is based on something it does not contain.

Work that predates plans keeps a path through. A branch with no recorded
plan, under a card on the application base, is **adopted**: its base is
read back out of git and recorded as adopted, so legacy runs keep working
and nothing invents a provenance they never had. Falling behind a base is
the normal condition of every branch whose base moved on, so that is never
read as "cut from somewhere else". The one ambiguous case is a card that
says its work is stacked on another branch while the branch does not
contain that branch at all, and that gets the refusal rather than a guess
— bench will not rebase a branch to make a claim true.

Two git identities live in this design and the plan keeps them apart:
**application git**, the repository a branch is cut in and a PR is opened
from, and **work-state git**, the checkout holding `tasks/`, where a move
commits itself and sync fast-forwards `main`. One layout answers both with
the same checkout, and asking through separate doors is what keeps
filesystem layout from changing branch-selection semantics.

## Execution profiles and approved context

A plan says *which checkout* a run works in. A **profile** says what that
run may do there, and which files it is guaranteed to have read. The two
compose and do not overlap, and both are resolved, validated and written
down before a billable model is invoked.

Before this, the second half was implicit and scattered.
`BOARD_AGENT_COMMANDS` was compiled into permission rules for `work` and
`act-pr` only, and `review` got a hardcoded grant set inside each
adapter. A prompt could *point* at a workflow brief outside the checkout,
but nothing checked the file was there, nothing recorded which revision of
it the run saw, and nothing stopped it changing under a run in flight. So
three things had no answer at all: what exactly may this run run, which
files is it guaranteed, and what did *this* run ride as against what the
settings say now. One resolved profile answers all three.

### Four facts that used to be one

The old arrangement could not be extended because it conflated four
different facts. They are separate now, and the data says which is which:

- **UI registration** — a `manager/local/commands/` executable is a `$`
  chip a person clicks. Appearing there grants a headless run nothing:
  resolved profiles list those under `uiCommands`, every one marked
  `agentCapability: false`.
- **Agent capability** — a named command grant. The only thing that
  reaches an adapter's permission rules.
- **Prompt instruction** — what the run is *told*. Prose persuades; it
  does not enforce, and `guarantees.promptOnly` says so out loud.
- **Gate requirement** — what a later verification or merge gate will
  insist on (`verify`), and what should be prepared before a launch
  (`prepare`). Declared, surfaced and recorded here; both resolve
  `pending`, because nothing enforces them yet.

### Where a profile comes from

Two structured inputs, in this order, and nothing else:

1. **A profile document** — `manager/local/profiles.json`, or wherever
   `BOARD_AGENT_PROFILES` points. Machine-local by construction: it names
   roots on *this* disk and capabilities *this* adapter declares, so it
   lives with the tool and never in the application's history. The whole
   schema is documented in `manager/core/profiles.example.json`.
2. **The legacy settings** — `BOARD_AGENT_COMMANDS` plus each adapter's
   built-in git/gh grants. A board with no document resolves exactly as it
   did before profiles existed, marked `source.kind: "legacy"`. That is
   the whole of "no new metadata keeps established behaviour", and it is
   why an update changes nothing until somebody writes a document.

A profile **narrows and never widens**. `publication.commit/push/publish`
may be turned off for an intent that has it and never on for one that does
not — `review` asking for push is refused by name — and the built-in
git/gh grants per intent stay the adapter's own knowledge. A document that
does not parse, or declares a schema version this bench does not read, is
a blocker on *every* intent and grants nothing at all: an operator who
mistyped a policy must not be handed the permissions they were in the
middle of changing.

### What it says, and how strongly

A resolved profile names the launch intent, the approved execution roots
(`worktree`, `app`, `work`, `tool` — words, not paths, because the
document is read on a machine whose paths are its own), the working
directory it runs in, the writable locations, each named command grant
with its executable and validated argument list, the approved readable
inputs, the explicitly denied capabilities, and what the profile
*requires*.

A command grant is one executable and fixed arguments. Anything that makes
one command line into two, or makes its text depend on the shell, is
refused where the document is read rather than by the process that would
have run it: compound commands, pipelines, redirections, globs, backtick
and `$(…)` substitutions, a relative executable, a path climbing out with
`..`, a malformed or duplicated grant name, an unknown execution root, and
a root the intent does not have (a read-only run has no worktree).

And **how strongly** anything holds is a separate answer, in four words
used everywhere: `enforced`, `prefix-allowlist`, `prompt-only`, `pending`.
A grant becomes a prefix allow-rule in the adapter's rule language. That
narrows which command lines a run may launch; it is **not** a sandbox,
nothing confines a process already running, and the `guarantees` block is
the single place that says so. Each adapter declares what it can actually
enforce in a `capabilities.json` beside its `run`; both shipped adapters
declare `filesystem-sandbox` unsupported, in those words. `requires` is
how a profile refuses to run without a real guarantee: every word in it
must resolve to something the installed backend genuinely does, and a
profile requiring one nothing enforces is **refused** rather than run
under a restriction that exists only in the prompt.

The same honesty holds where it is read first. The writable locations
are the **intended** write scope (`"enforcement": "intended"`), and the
profile carries a separate `isolation` answer read from the same
capability table: with `filesystem-sandbox` unsupported it says *not
enforced*. The ⚿ sheet's summary draws them as two rows — **Intended
writes** and **OS isolation: none enforced** — and the Focus panel says
the same in one line, so the headline never reads as a boundary that the
folded technical detail then retracts.

### Approved context: pinned, hashed, and nothing beside it

A project selects a set of files from the tool and work-state roots, and
they are delivered as verifiable readable inputs. Three things in order
make that a guarantee rather than a hope: the path is resolved inside a
named root, the content is hashed, and a read-only copy is pinned under
`manager/local/state/context/` before the model is invoked. The prompt
then names those files by the path they were pinned at — *in addition* to
the workflow brief it already points at, which is never replaced,
reworded or summarised.

It fails closed, every time. A required input that is missing, unreadable,
a directory, over the size cap, named like a credential, absolute, using
`..`, or reachable only through a link out of its root **refuses the
launch** — by input name, with a remedy, before any model, and leaving the
card with no branch, no worktree, no claim and no run. A directory grant
is refused *as* a read-only guarantee, because it lets a run read whatever
is dropped in later. `"required": false` turns that refusal into a note
and changes nothing else.

Approval covers those files and nothing beside them. It is not permission
to edit them, to read what sits next to them, or to publish their
contents — and no diagnostic ever quotes one: refusals name paths,
revisions and hashes, never bytes.

What the copy guarantees is worth stating narrowly, because the obvious
phrasing claims more: an edit to the source cannot change what a run was
given. The bundle is written read-only, but a run with a shell could
still alter its own copy, which is why the hash on record is of what was
pinned rather than of what it was meant to be. That sentence is in the
capability table the board shows, in those words.

### One manifest, for the whole of the run

The resolved profile is recorded with the run, in
`manager/local/state/manifests/<agent id>.json`, and it holds the policy
revision and its source identity, each input's source revision and content
hash, the working-directory policy, and the adapter and resolver versions.
Machine-local for the plan's reason: it holds this machine's paths.

Four consequences, and they are why the record exists:

- **A later change does not repin an existing run.** Edit the document, or
  the approved file itself, and the run keeps what it rode; the next
  launch gets the new answer. `freshness()` is what reports the
  difference, and it never resolves it.
- **The page reads the run's manifest, not the settings.** The Focus
  view's **Permissions & inputs** panel is fed by the frozen record and
  says "settings have moved" when they have, so "what the settings say
  now" can never be read as "what that run rode".
- **One resolver answers both.** The pre-launch reading and the launch
  itself call the same function, so a page cannot show a permission a run
  would not have had.
- **Later members have identities to refer to.** Policy, context, request
  and evidence identities are defined once (`profiles.seams()` names them,
  along with every pending capability and what a runner, a doctor, a gate
  and a reviewer each plug into), so none of them invents a second policy
  schema.

### On the board

A `⚿` chip in the card's footer row, beside `⌖` and `CI`, opens the
profile preflight: where the run would run, what it may write, every named
command grant, every approved input with its readable status, what is
blocked, and — at the top, in `--alarm` — any blocker with its remedy,
before a launch. The sheet switches between the four intents, because
which one a card hosts depends on what you are about to click. It is
read-only: it resolves and hashes and pins nothing, so opening it costs
nothing.

The chip appears only where there is something to say. A board resolving
on the legacy settings has no such chip anywhere — the same law the plan
chip follows — and a document that does not parse wears the alarm and the
reason. Technical detail (identities, hashes, the settings precedence, the
capability table) folds away rather than leading, and an approved
document's contents are nowhere on the page.

## Verification: bench runs the check

### Start here: you provide the checks; bench runs them

**Verification means checking the work, rather than taking the agent's word
for it.** For a website, that might mean running tests and checking that the
site builds. For a data project, it might mean checking that the output has
the required columns and contains no invalid records.

Bench does not know what makes *your* application correct. **You or your
project team are responsible for providing and maintaining the programs
that check it.** You can reuse existing test and build commands; you do not
have to invent a special Bench testing system. If you are not a developer,
ask your developer or coding agent to create the checks, explain their
coverage and limitations, and demonstrate that they fail when something
is wrong. An agent can help write them, but that does not remove your
team's responsibility to decide whether the checks are sufficient.

For example, imagine an agent changes a customer sign-up form:

1. Your team provides a verification program that tests sign-up and checks
   that the application builds.
2. The person configuring Bench registers that program as a **named check**
   and approves where and how it may run.
3. The agent asks Bench to run the check, or you request it from the board.
4. Bench runs the approved program against the relevant saved version of
   the code and records the result itself.
5. If you have configured a **gate** requiring that check, Bench will not
   allow the guarded merge until the required current evidence passes.

**The program does the checking. Bench runs it, records the evidence and
applies your acceptance rules.** Bench does not manufacture tests for a
project simply because you install it or create a card.

### Who is responsible for what?

| Responsibility | Who owns it? |
| --- | --- |
| Decide what correct behavior looks like | You and your project team |
| Write and maintain tests, build checks and verification scripts | Your developers, with agent assistance if useful |
| Approve runnable commands, test locations and required checks | The operator: the person who configures this Bench installation |
| Execute an approved check and record its result and code version | Bench |
| Enforce the configured conditions before integration or acceptance | Bench's gates |
| Judge whether the checks cover the important risks and review the work | You and your reviewers |

### The terms, without the machinery

- **Verification program:** an ordinary script or command that checks the
  work. It might run your existing test suite, build the app or validate
  data. A **named check** is the label Bench uses for an approved invocation
  of that program, such as `unit` or `build`.
- **Preparation program:** a command that gets a disposable test environment
  ready, such as installing approved dependencies or preparing a test
  database. You provide this too when it is needed. Preparation failing
  means the checks could not run; it is not a failing test result.
- **Scope:** the portion of the work a check covers, such as `backend`,
  `frontend` or `all`. These names and their arguments are configured by
  the operator. A backend-only pass does not satisfy a gate requiring all.
- **Evidence:** Bench's record of what actually ran, its result, and the
  code and checking-program identities it applies to. An agent's sentence
  saying “tests passed” is not this record.
- **Gate:** a condition enforced before a particular action, such as
  merging a task into a phase or accepting the completed phase. It can
  require checks, review approval, or both. Registering a check does not
  automatically make it a required gate.
- **Stale:** evidence that no longer establishes a pass for the current
  work. Changing the code, approved inputs or checking program can make
  an earlier result stale and require a new run.

### What you need to supply

Start with the commands your team already uses to test and build the
project. Ask the developer configuring Bench to:

1. Identify the checks that matter and explain what they do **not** cover.
2. Make them run without interactive questions and return a failing exit
   status when a check fails. A wrapper must not hide a failure by printing
   “OK” or exiting successfully after a failed command.
3. Supply any required preparation steps. Use disposable test data, not
   your live database, and document required dependencies and access.
4. Register the approved commands and scopes in the local execution-profile
   configuration, then separately choose which checks a gate must require.
5. Demonstrate both a passing case and a deliberately failing case in a
   safe fixture. Check that Bench shows the correct outcome and blocks the
   required gate on failure.

The configuration example below is for the person doing that setup. You do
not need to understand its JSON to ask your team: **“Which checks are we
providing, what do they cover, and which ones must pass before acceptance?”**

As new tests are added, an existing test-suite command may discover them
automatically. A new test family, dependency or kind of check may instead
need an updated program and configuration. Bench does not automatically
invent that missing coverage.

### What a pass does — and does not — tell you

Bench normally uses the verification program's exit status to determine
whether it succeeded. **A pass means the supplied check passed, not that
Bench has proved the application is bug-free.** A check that tests the wrong
thing, skips important cases or always exits successfully gives weak
assurance. Human review and any required manual checks still matter.

If preparation fails, permission is denied or the check is cancelled,
Bench records that different outcome rather than pretending the tests
passed. A previously passing result must also be relevant to the current
code and required scope. If a full check fails, a passing backend-only run
cannot stand in for it.

Checks execute programs. A separate checkout and command permissions are
not an operating-system sandbox: do not assume an arbitrary test script
cannot access other files or services. Use the documented trust limits
below; guarantees the installed backend cannot enforce must not be treated
as protection.

**Existing projects remain compatible:** with no configured gate, Bench
keeps its ungated behavior. Nothing is required and nothing is proved by
that decision. To make verification mandatory, configure the appropriate
gate as well as the check.

### How named checks work

A plan says which checkout a run works in; a profile says what it may do
there. This is the question those two leave open: **did the project's own
checks actually pass, and against what?**

"The tests passed" used to be a sentence in an agent's report. The board
had given the run a *prefix* — `python3 -m unittest` — and a shell, and
then believed what came back. A **named check** is the other
arrangement: the run asks for `unit-tests`, bench reads the operator's
document, builds the argument list itself, runs it with no shell, and
records the exit status its own `wait()` returned before the asking run
is told anything.

Three things follow, and they are the whole feature:

- **Nothing a model writes reaches an argv.** A name is all a run can
  choose. There is no command line for anything to be appended to, and a
  scope is a *declared name* too (`--fast`, a directory, a `-k` filter is
  the operator's to write, never the run's).
- **An exit code read by the thing being judged is not evidence.** An
  agent exiting zero cannot make a check passing, and a check nothing
  asked for is `not-run` rather than silence.
- **"It failed" is not one fact.** A test that failed, a dependency that
  would not install, a permission that was refused, a cancellation and a
  timeout are five different things to do next, so they are five
  outcomes rather than one non-zero.

### What a document declares

Two lists at the top level of the profile document, and a name per intent
— the whole schema is in `manager/core/profiles.example.json`:

```jsonc
"checks":  [{"name": "unit", "run": "python3 -m unittest discover -s tests",
             "root": "worktree", "scopes": {"fast": ["-k", "fast"]},
             "prepare": ["deps"], "timeout": 900}],
"prepare": [{"name": "deps", "run": "python3 -m pip install -e .",
             "network": true}],
"profiles": {"work": {"verify": ["unit"]}, "review": {"verify": ["unit"]}}
```

A check is validated where the document is read — in the policy layer,
beside the grants and the inputs, and ahead of any process. It is held to
everything a command grant is held to and three things more, each because
*bench* is the one running it:

- **The application checkout is not an execution root.** `worktree` is
  the card's own checkout, which bench made; `source` is a detached
  checkout of one exact commit that bench makes for the verification and
  owns; `review` is the isolated checkout pinned to the pull request's
  exact proposed commit for a PR-review run. A `review` check requires
  that provisioned checkout; it never falls back to the live application.
  These are execution locations, not security sandboxes. See the trust
  limits below before running project code.
- **A check verifies; it does not reshape the repository.** `git diff
  --check` is exactly the sort of thing a check is; `git push`, `git
  reset`, `git worktree`, `git config` and the rest are refused by name,
  because the execution root shares its git metadata with every other
  worktree of the application.
- **An argument naming a credential is refused**, as a context selection
  is — `pytest --env-file .env` is refused as an argument rather than as
  a file.

A check *may* be quoted where a grant may not, and the difference is
worth keeping straight: a grant becomes a prefix allow-rule and a rule
cannot see through a quote, so quoting there would widen what it matched.
A check is never rendered into a rule — bench splits it once and hands
the result to `execve` — so a path with spaces is expressible exactly the
way a shell expresses one.

### How a run asks

One executable, granted by bench itself and present only where that
intent has a check to ask for: `manager/core/bench-verify`. With no
arguments it lists what the run may ask for; with a name it runs it; with
a name and a scope it runs that scope. Its exit status says which *kind*
of answer came back — `0` passed, `1` the check itself said no, `2`
refused, `3` preparation failed, `4` no verdict reached, `5` the board
could not be asked — so only `1` is a test failure.

Because it is a grant, both adapters render it, **`review` included**.
That is the one rule this changed: a profile's named grants now reach the
read-only intent, while `BOARD_AGENT_COMMANDS` still does not. A review
that can ask bench to run the tests gains no commit and no push rule
anywhere in the generated document, which is the separation
`publication` existed for.

### The compatibility path, in one sentence

A `verify` entry naming one of the intent's own **command grants**
resolves to a check built from that grant. So a document written before
bench could run anything keeps working and its declared requirement
becomes a real run — while a `verify` naming *nothing* is now refused,
because a requirement that resolves to `pending` is indistinguishable
from a check that will never run. A board with no document has no checks
at all and resolves exactly as it always did: its agents run the
project's commands themselves, through the grants they have always had.

### What a check is given, and what that is worth

Said narrowly, because the obvious phrasing claims more. Bench
**enforces** the argument list, the absence of publication credentials
(removed from the child's environment by name — a process cannot read
what is not there), the directory the check starts in, and that a check
never runs in the application checkout. Bench **directs** where caches,
virtualenvs, `node_modules`, build output and temporary files land, by
pointing `HOME`, `TMPDIR` and the cache variables into a directory made
for the verification. That is not a jail: a check with a path can still
write elsewhere. `filesystem-sandbox` is the capability that would make
it one, no shipped adapter or backend enforces it, and a profile that
*requires* it is refused rather than reported as guaranteed.

Two more absences are deliberate. The **launch environment** (`AGENT_*`)
never reaches a check, so it cannot read the prompt or the resolved
policy; and neither does the board's own address (`BOARD_PORT`,
`BOARD_AGENT_ID`), so a check cannot turn round and ask for another
verification. A preparation step that declares `"network": true` runs
with whatever network the machine has and the evidence says so —
`network-policy` is pending, and a profile requiring it refuses.

### The evidence

One JSON record per verification under `local/state/evidence/`, written
when the request is accepted and rewritten when it ends, with the
check's whole output beside it. It holds the command and its argument
list, the execution root and whether bench made it, the exact source
(the commit, the branch, and a digest of everything uncommitted), the
policy, check, context and request identities, the names removed from the
environment, every preparation step's own outcome, the timestamps, the
exit status and the log. Machine-local for the manifest's reason, so the
board says `local to this board` rather than implying a teammate can see
it.

Four things it guarantees:

- **It never drifts towards a pass.** A record still marked `running`
  when a board starts is evidence that board does not own, so it is
  closed as `error`, naming the restart. Nothing is re-run: asking again
  is a person's decision, and an automatic retry at boot is how an outage
  becomes a thundering herd.
- **A pass is bound to what it was about, and to nothing else.** Every
  new record carries a **result identity** (`resultId`): a digest of the
  source, the check's substance (its argument list, root, time limit and
  declared inputs and environment — not its name), the scope's
  arguments, the programs, the preparation (each step's argument list,
  network flag, whether it is required, and its program), the declared
  inputs' content, the root class, the declared environment and the
  runner. Modified tracked source, a new untracked file, a new commit, an
  edited check, a changed scope, preparation, input or environment
  variable make a recorded `passed` read `stale`, each with its own
  reason — `source-changed`, `check-changed`, `scope-changed`,
  `prepare-changed`, `program-changed`, `input-changed`, `env-changed`,
  `runner-changed` — naming the input, variable, step or scope, with a
  remedy. A changed **policy** on its own does not: which policy revision
  was in force, the context pinned for some other card, who asked, the
  model and the temporary paths are **provenance**, kept in the record's
  `provenance` block and shown, never a reason. So approving a document
  for the next card no longer sends this card's checks round again.
  A record written before result identities existed has no `resultId`
  and keeps the rules it was written under, byte for byte, including
  `policy-changed` and `context-changed`; one fresh run replaces it.
  An identity that cannot be read is `identity-unknown`, never a pass.
  So does a changed
  **program**: the record hashes the executable as it resolved on the
  check's PATH, the interpreter its `#!` line names (one level), and every
  absolute-path argument outside the execution root (`python3
  /opt/ci/check.py`), and the same for each preparation step — so a file
  replaced at the same path is a different check. It records bench's own
  **runner revision** too, and evidence from another runner, or a record
  naming no program identity at all, is never reused (`program-changed`,
  `runner-changed`). What that pins is what was invoked; it does not bind
  the libraries, modules or services those programs load, and it is not a
  sandbox. `freshness()` compares and never resolves; `outcome_now()` may
  only ever disagree with the record in the safe direction.
- **What a check reads, it declares.** A check's optional `"inputs"`
  names selections from the document's top-level `"context"` list (from
  the `tool`, `work` or `app` root — the checkout it runs in is its
  source, and is bound as that). Bench reads and hashes each one when the
  check is asked for, hands it over as a read-only copy in the directory
  `$BENCH_VERIFY_INPUTS` names (`<name><suffix>` inside it), and records
  the hash of exactly what it handed over. A required input that cannot
  be read refuses the request (`input-unavailable`) and runs nothing; an
  optional one that is not there is recorded as absent, and its
  appearing later is a change. An input the context does not select is
  refused where the document is read (`check-input-undeclared`). A
  check's optional `"env"` names environment variables whose **values**
  its result depends on (`check-env-invalid` refuses a non-name, a
  duplicate, a credential bench removes and anything bench sets itself).
  Their values are bound by HMAC-SHA256 under a key that exists only on
  this machine (`local/state/verify/env.key`, mode 0600, made by the
  runner the first time a check declaring `env` runs — never by reading
  freshness), so the record proves "the same value" to this board and
  says nothing to anyone else. A lost or replaced key reads `env-changed`
  and costs one fresh run of each such check. The names of every variable
  a check is given are bound too; undeclared values are not.
- **What it does not bind, said plainly.** Bench pins what a check is
  given, not everything it reads: a file read by an undeclared path, the
  libraries and modules its programs load, ignored files in a `worktree`
  root (the identity records `worktree`, `fresh` or `reused` as its root
  class, and calls a `source` or `review` checkout `fresh` only when the
  runner found it holding nothing beyond the commit), the values of
  undeclared variables and any service it calls. Equal identity means
  *the same declared thing ran on the same code*, not the same assurance
  in every possible world; declare `inputs` and `env` for what matters.
  And a valid result is evidence, never permission: whether a check may
  be run is still the profile's answer at every request, and what a
  merge requires is still the gate policy in force now.
- **Secrets do not reach a reader.** The record names paths, revisions
  and hashes and never content or an environment *value*; the keyed
  hashes of declared values stay in the record on this disk and are left
  out of everything the page is sent; the key is never written anywhere
  but its own file; the log — the one place bytes bench did not write
  get through — is scrubbed of anything credential-shaped on the way out.
- **It outlives the run, the worktree and the board.** Holding a run
  cancels the verifications bench was running for it, recorded as
  cancelled rather than as failures; a duplicate request for a check
  already in flight joins it rather than doubling it; and a `source`
  checkout with uncommitted work in it is kept and reported rather than
  forced out.

### On the board

A `◫` chip in the card's footer row, beside `⚿`, `⌖` and `CI`, opens the
evidence: every named check with its outcome **in words**, its scope, the
commit it ran against, how long it took, its preparation steps drawn
apart from the check they prepared, and a safe link to the scrubbed log.
Colour means state as it does everywhere here, but the word is what
carries the meaning — a test failure, a permission denial and a
dependency failure must not collapse into "red".

The sheet holds every run, required or optional, so its chip is labelled
`◫ evidence` rather than with a count: the headline count is the merge
requirements' (`⚖`), and a count of every check the policy offers beside
it could only contradict it. The chip is on the face only beside a check
that failed, could not run or is running on the current commit.

The Focus view gains a **Verification** panel beside **Checks**, and the
pair is the point: `Checks` is classified from commands the *agent* ran —
the run's own account of itself — and this one is the runner's, every row
a process the board launched and waited on. A finished agent with nothing
there reads `not run`, in those words, and says so.

Opening either runs nothing. Rerunning is an armed `↻` per check in the
sheet, with its side effects written out beside it: the check's command
in the card's own checkout (or a detached one bench makes and removes),
preparation first, a new evidence record and a new log — no card moved,
no branch touched, nothing published, and a run already in flight joined
rather than doubled.

## Gates: nothing merges on a report

A gate is an acceptance rule you configure: for example, “the full test
suite must pass before this phase can merge.” Your team supplies the
verification programs; Bench uses their recorded results to enforce the
rule. A gate can also require a review. Adding a check and making it
mandatory are separate choices. With no gate configured, there is no
required verification barrier.

Verification records what the project's checks found. This is the
question that record exists to answer: **may this merge happen?**

Three things used to answer it, and not one of them is evidence. A
process exited zero — which is the thing being judged reporting on
itself. Commits appeared on a branch — which says work was done and
nothing about whether it works. And no CI result came back, which the
phase runner read as *advance*, so that a project without CI could not
deadlock every phase it ran. That last one was honest when it was
written and it is exactly the case a configured gate exists to stop: a
member whose checks were refused before they started can reach `review/`
with committed code and be merged into an integration branch by a beat
nobody was watching.

So a board that declares a gate gets one rule, and it is narrow. **The
mutation runs when bench's own recorded evidence reads `passed` for every
required check, against the exact commit about to be merged, and when the
review policy the operator chose is satisfied by a record that genuinely
says what it is read as saying.** Anything else is reported — by name,
with the remedy — and the mutation does not run.

A board that declares *no* gate is every board that existed before this,
and it behaves exactly as it did. Its decision reads `ungated`, in that
word, which is deliberately not `allowed`: nothing is required there and
nothing is proved, and a reader is told so rather than shown a colour
that could pass for a pass.

### Three scopes, because three different things merge

A gate is declared per guarded mutation, in the profile document's
top-level `"gates"` block — validated where every other policy value is
validated, beside the grants, the inputs and the checks:

```jsonc
"gates": {
  "member":      {"checks": [{"check": "unit", "scope": "fast"}]},
  "integration": {"checks": ["unit"], "review": "bench"},
  "acceptance":  {"checks": ["unit"], "review": "separate-reviewer"},
  "overrides":   {"allow": false}
}
```

- **`member`** — a phase merging one member's branch into its integration
  branch. The automatic step no person clicks, and the one that used to
  advance on an absent CI result.
- **`integration`** — a phase's own integration head being accepted. That
  is a *different commit* from any member's, which is why it is a scope
  of its own: the head every member was merged into has never been
  checked by any of them, and every member passing proves nothing about
  it. Ancestry containment, already-merged member PRs and a quiet CI
  prove nothing about it either.
- **`acceptance`** — an ordinary card being merged and cleaned up: the one
  place work leaves the board.

The same law the rest of the document follows applies here. A gate
requiring a check the document declares nowhere is **refused where the
document is read** rather than blocking every merge later, because a
requirement nothing can satisfy is indistinguishable from no requirement
at all. A scope that is not a scope, a key a gate has no meaning for and
a review policy bench has no word for are refused the same way, each
with the code it came from and a remedy for the class of it. And a
document with *any* refusal in it gates everything shut rather than open:
an operator mid-edit is not handed the gate they were in the middle of
changing.

### A requirement is a check and the coverage it needs

A requirement is written two ways. A bare name — `"unit"` — is the
**unscoped full run** of that check, and it is what every gate written
before scopes existed keeps meaning. `{"check": "unit", "scope": "fast"}`
is that declared scope. A scope the check does not declare, or a
requirement object with any other key, is refused where the document is
read (`gate-scope-undeclared`, `gate-invalid`).

Coverage is matched, not assumed. The full run covers every scope of its
check; a scope covers only itself. So a narrow pass never satisfies a
broader requirement — `unit backend` passing says nothing about `unit` —
and a gate that needs both says so by requiring both.

**Relevance before precedence.** The gate fixes the commit it would
merge *first*, and only then weighs the records about that commit. A
record is relevant when it is about this check, its coverage overlaps the
requirement's — for a full-run requirement every run of the check, for a
scope that scope and the full run — and it ran against this commit. A
request still preparing has not bound its source yet and counts as
relevant. Everything else is **history**: kept in the requirement's
history, counted (`otherCommits`), and never the reason. So an older
commit's cancellation cannot become a newer commit's blocker, and a
failure on a commit nobody is merging blocks nothing — the requirement
reads `other-commit`, *"has not run on H"*, and names what the older
commits said. A bench that cannot name the commit refuses with
`head-unknown` before any record is read for a reason.

**Among the relevant records, the newest answer is the gate's answer.**
A failed, errored or cancelled rerun supersedes an earlier pass, and one
still running holds the gate `pending`. A pass that has gone **stale** at
this commit is an answer too: the requirement reads `stale` with that
pass's own reason (`staleReason`, such as `check-changed` or
`input-changed`, or `policy-changed` on a legacy record), it is never a
pass, and nothing older at the same
commit is resurrected past it. Passes with uncommitted work under them or
no bound source did not run against this commit, narrow passes do not
cover the requirement, and denied requests ran nothing, so none of them
supersede anything. Ordering is request time, then end time, and on a
tie a non-pass reads as the newer — never the order files were listed
in. The ⚖ sheet names each requirement's coverage (`unit · full run`,
`unit · scope fast`), so a ◫ chip reading a scoped pass beside a gate
requiring the full run is never left unexplained.

**A record about this commit is current, outdated or legacy.** One with a
result identity (see "The evidence") is *current* while everything its
definition depends on — the check's substance, its coverage, programs,
preparation, declared inputs, declared environment and the runner —
still holds, and *outdated* once any of it moved. Outdated records decide
nothing: a failure under a command that has since been replaced is not
today's failure, and if nothing current decides, the requirement reads
`stale` with the newest outdated record's own changed dimension and its
remedy. A record with no identity — anything written before card 97 — is
*legacy*, read with the freshness rules it was written under
(`verify.outcome_now`), unchanged, `policy-changed` included. An identity
that cannot be read is outdated, never current. The other seam card 94's
design plugs in — an operator-declared equivalent check that must also
match the check's substance — still answers "nothing", so no record of
another name satisfies a requirement.

### Evidence is bound to the commit

A required check is met by one thing only: a record `verify.py` wrote,
which `verify.outcome_now()` still reads as `passed`, whose source was
**bound** and **clean**, whose commit is the commit about to move, and
which is the newest relevant answer for the requirement's coverage.

The clean-source condition is the one that is easy to miss and hard to
argue with. A check that ran with uncommitted files under it did not run
against the commit that merges; it ran against something that existed on
one disk. So the order on a gated board is **commit, then ask** — the
agent's prompt says so where a gate is configured — and committing again
means asking again.

Everything else is unmet, and *which* way it is unmet is the whole
difference between the sentences a reader needs:

| reason | what happened |
| --- | --- |
| `not-run` | nothing asked bench for the check. An absent record is not a pass |
| `failed` | the check ran and said no |
| `prepare-failed` | a dependency would not install, so the check never ran |
| `denied` | the request was refused before anything ran |
| `cancelled` | it was stopped before reaching a verdict |
| `error` | no verdict was reached — a timeout, or a board that restarted |
| `stale` | it passed, and what it was about has changed since |
| `other-commit` | it has not run on this commit; what is on record ran against others, and is history |
| `source-dirty` | it passed with uncommitted work under it |
| `source-unbound` | the source could not be bound, so it proves nothing |

Each carries its own remedy, because "ask bench to run it", "read the
log and fix what it found" and "the dependency step is the thing to fix"
send a person to three different places. A check that is *running* is
`pending` — the mutation waits, and nothing is decided from an answer
that is not in yet.

### Enforced at the mutation, and read twice

Not in the UI. The page reads the same decision the mutation reads, from
the same function, so a disabled action and a backend refusal can never
give two different reasons for one state:

- **`phases` holds a member out of `ready`** while its gate is unmet, and
  halts the phase with the gate's own sentence as the reason — the
  existing halt, which stops rather than skips and waits for a person.
  Then it asks **again, immediately before the merge**, with the branch
  tip in hand: the snapshot was taken a pass ago, and a push landing in
  between is a different commit.
- **Merge & clean up asks before it parks the drive** — cheap, and it
  leaves the card, the branch, the worktree and every record untouched —
  **and again immediately before the merge**, for the same reason. That
  second read narrows the stale-evidence window; what closes it is below.

**The merge takes the commit the gate read.** Reading twice is not an
atomic mutation, so on a gated board the validated head itself flows into
the merge: the local merge and a phase member's merge take that SHA
rather than the branch name, and a merge on origin runs `gh pr merge
--match-head-commit <sha>`, which GitHub refuses if the pull request's
head has moved. A `gh` that does not offer the flag is refused by name —
there is no unconditional fallback over a gate. Cleanup is held to the
same commit: a branch that gained commits after the gate read it keeps
its worktree and its local branch, and the remote branch is deleted only
while `ls-remote` shows nothing but what was merged, with a
`--force-with-lease` on that exact value so a push landing in between
makes git refuse. A phase member's workspace is cleared under the same
rule against the commit the phase merged. An ungated board merges and
cleans up exactly as it always did.

Reading a gate runs nothing: no check, no relaunch, no card moved, no
branch touched, nothing reaching a remote. Which is also why nothing
retries by itself. An unmet gate is a human decision point, for a dead
run's reasons — an outage would make an automatic rerun a thundering
herd — so the remedy *names* the rerun path (the armed `↻` per check in
the evidence sheet) and does not take it.

A replica evaluates and renders exactly as the actor's board does, and
advances nothing: the actor rule is the phase runner's, and
merge-and-clean-up is a person's click.

### Two records of a review, and a COMMENTED review is neither

A gate's review policy is one of four words, and the last three are not
interchangeable:

- **`none`** — the default; no review is required by this gate.
- **`bench`** — a **Bench review verdict** of APPROVE, recorded by bench
  itself against this exact head. Independent of GitHub by construction:
  **◔ review PR** records what its reviewer found whether or not
  `gh pr review` accepted anything, so a board with no `gh` at all still
  has verdicts, bound to the commit `reviews.py` pinned.
- **`github`** — a **formal GitHub review state** of APPROVED at this
  commit. Read from GitHub's own review objects, which carry the commit
  each review was given for; an approval that cannot be bound to a commit
  could only be bound by guessing, and a gate that guessed would be worse
  than none.
- **`separate-reviewer`** — that approval authored by somebody other than
  the account that proposed the head.

**An outstanding change request blocks, whoever else approved.** Per
reviewer, the latest formal state stands — `APPROVED`,
`CHANGES_REQUESTED` or `DISMISSED`, ordered by submission time. A later
review from the *same* reviewer supersedes their earlier one, and a
dismissal on GitHub clears it; another reviewer's approval clears nobody,
and a `COMMENTED` review supersedes nothing. On a stale head the reading
is conservative in both directions: an approval counts only at the
commit it was given for, while a change request on an earlier commit
stays outstanding until it is superseded or dismissed. This is bench's
reading of the review objects the poll last saw — it is **not** GitHub's
branch protection, whose required reviews, code owners and dismissal
settings GitHub applies separately when it merges, and the sheet says so.

**The same-account limitation, said out loud.** GitHub does not accept an
approval or a change request from the account that opened a pull request.
A board whose reviewer runs as that account can only leave a *comment* —
and bench reads it as one, however its body is worded. So `github` and
`separate-reviewer` read unmet there, with the limitation named as the
configuration fact it is and `bench` offered as the policy for a project
with one identity. Bench never switches accounts, asks for a second
credential, or downgrades a formal-review requirement to a comment; and
where the reviewer's own log shows GitHub refusing the attempt, that is
recorded as a note on bench's verdict rather than as a verdict or a
review state.

### An override is explicit, bound, and never a pass

A gate can be proceeded over, and only through a path the policy turns on
(`"overrides": {"allow": true}`, optionally restricted to named git
identities). What makes it safe to have at all is what it writes down:
the actor, the reason, the exact commit and the exact gates that were
unmet when they decided.

It forges nothing. No check becomes `passed`, no verdict appears, and the
decision afterwards reads `overridden` — which wears the alarm, not the
colour of a pass, and says in the same breath that the gate is still
unmet. It authorises **one commit**: the next one is a different
question. It lapses rather than stretching, so a gate that goes unmet
*after* an override covers none of it. A repeated click, a replayed
request and a board that restarted mid-confirmation produce one record,
not two. A confirmation about a head that has since moved is refused. And
a document with a refusal in it offers no override at all.

**An override needs a commit to be about.** A card with no implementation
commit — no branch yet, or a branch application git cannot read — has
nothing an override could authorise, so it is refused before anything is
written: *there is no implementation commit to override at, so nothing
was recorded*. The override route also refuses a request that names no
head at all, so a direct call cannot authorise whatever the branch holds
by the time it lands.

**Whether an override is possible is the server's answer.** Every gate
reading carries `overrides.eligible`, and `overrides.ineligible` with a
code (`not-allowed`, `actor-not-named`, `no-actor`, `in-force`,
`nothing-unmet`, `no-target`) and the sentence to show. The gate sheet
asks it for this board's own identity and draws it: an eligible gate
offers the armed confirmation; an ineligible one shows **Override**
disabled with that sentence. The mutation refuses from the same function,
so the sheet and the refusal cannot disagree.

**It is bound to the rule it overrode, too.** A new override records the
identity of what its scope required (each required check's definition
and coverage, the review policy, and who may override), and it stops
applying when that changes — the person decided about the rule as it
stood. An unrelated policy edit, such as an approved context file or
another scope's gate, does not withdraw it. An override recorded before
this binding existed keeps exactly the meaning it was recorded with.

### On the board

A `⚖` chip in the card's footer row, beside `⚿`, `⌖`, `◫` and `CI`,
opens the merge requirements: the card's delivery status — the same
word, sentence and primary action the face and the Overview draw — then
`m of n merge requirements met for <commit>`, one row per required
obligation with its state and, for a problem, its remedy, and folded
under it the optional checks and earlier runs, and every scope's
reading against the commit that would move. Present only where a gate
is configured, the law every other chip here follows. Its label is the
required obligations only (`2 needed`, `✕` for a current problem,
`2/2` when met), and it is on the face in `review/` and whenever an
override is in force; before review, requirements that lie ahead are in
the tools, never the alarm (see "The card face and its tools").

The sheet keeps the two review records apart, because that is the
distinction the feature rests on: bench's own verdict with the head it
was bound to, GitHub's formal state with the commit each review was given
for, the comments drawn as comments, and the same-account limitation
spelled out where it applies. Where the policy enables one, a single
armed confirmation records an override, with the actor, the unmet gates
and the commit written out beside the field for the reason.

The Focus view gains a **Gate** panel beside **Verification**, drawing
the three states an exit code cannot tell apart — code produced, work
verified, work accepted. A phase's lane carries its integration gate at
the top of itself, beside where a halt is drawn, because the lane is
where "every member merged and green" has to stop reading as "ready to
accept". And the **⚕ Configuration check** reports, before anything is launched, what
each scope requires and every refusal in the gate block with its remedy.

The two records this adds — the Bench verdicts and the overrides — live
under `manager/local/state/gates/`, machine-local for the reason
`state/evidence/` and `state/reviews/` are: they hold this machine's
reading of a remote and this machine's paths, and nothing a teammate
pulls has any business carrying them.

## Delivery status: one answer per card

A person on the common path asks **what is happening, what do I do
next, and what, if anything, prevents delivery?** The face,
the Overview and the merge dialog each used to work those out for
themselves, and could disagree. `delivery.py` answers them once, on the
server, and every surface draws that one result: the card face's pill
and its ⚖ chip, the detail view's head and Overview, and the merge
requirements dialog (see "On the page", below).

It owns no policy and no enforcement. Every fact comes from the
authority that already answers for it: the card itself (`taskfiles`),
the agent registry and the failure a dead run left, a completion in
flight or merged-but-unpublished, the recorded plan, the commit the
merge would take and the gate's reading of it (`gates.candidate`,
`gates.evaluate`), the last PR observation, open review findings, and
the phase runner's last pass. **Mutations never read it.** Starting
work, verifying, overriding and merging keep their own checks and run
them again at the moment they act, so a stale reading can mislead
nobody into a merge.

### What one reading says

`GET /api/card/delivery?file=<card>` (always recomputed) returns, and
`/api/state` carries as `delivery` in compact form:

- **one status** — a word, its label, one sentence and a tone. The words
  and the precedence that picks one are data in the module (`WORDS`,
  `ROWS`), and the tests derive the vocabulary from them rather than
  counting it. Lifecycle comes first (merging, not published, a failed
  run, an agent or a phase working), then the stage, then — in review —
  genuine problems on the *current* commit before steps that simply lie
  ahead: `check-failed` and `cannot-run-check` before `checks-running`,
  `checks-needed`, `review-needed` and `ready-to-merge`. `cancelled`,
  `stale` and `not-run` are reasons inside `checks-needed`, never
  failures.
- **one primary action**, with who holds it (`you`, `agent`, `board`),
  whether it is available and, if not, why; and every other legitimate
  action in `others`, never hidden. An action whose handler belongs to a
  later card (**Run required checks**, sign-off) is always
  `enabled: false` with that reason — never a button that does nothing.
- **the candidate commit**, in one of four states read only from durable
  records — `present` (the branch is here), `none-yet`, `delivered` and
  `unavailable` (records of earlier work exist, its commit is not here).
  Agent memory decides none of them, so a restarted board reads what it
  read before. When the board has a remote, or the card is held by
  someone else, absence is said as *"on this board"*.
- **the requirements that apply**, each with a class from one shared
  table (`gates.SEVERITY`: met, needed, running, problem,
  cannot-assess) and a plain sentence (`gates.PLAIN`). **Counts are
  required obligations only**: optional checks and earlier runs are
  counted apart under `history` and never inflate a headline or block.
- **where it came from** — the PR observation's time and whether it was
  read from the poll's memory or the persisted snapshot (*as of 14:02*).

Before a card has a commit, its requirements are listed as *needed
before merge*, in the neutral register. A ready-to-start card never says
it *can* start: that is the launch's own preflight to decide.

A request for changes is a current problem only about the current
commit. Where a merge rule requires a review, its requirement row says
so; where none does, Bench's own `REQUEST CHANGES` verdict at the
current commit still reads `changes-requested` — the face used to say
"changes asked" from a cascade of its own, and drawing this result
instead must not lose it. A verdict about an earlier commit is history.

### On the page

The compact form carries everything the surfaces draw — the status, the
primary action and the alternatives, the candidate, the required-only
counts, the history counts and the requirement rows' classes — so the
face, the detail head, the Overview and the dialog read the same
answer, and none of them works out a status, a next step or a count of
its own. The full reading adds only the rows themselves, their remedies
and the technical provenance, and the page fetches it again only when
the compact answer it belongs to has changed.

- **The face** wears the status under the face rule in "The card face
  and its tools", and one `⚖` chip whose count is `counts`.
- **The Overview and the dialog** draw the same block: the word as a
  heading, the sentence (`role="alert"` only in the alarm tone), the
  primary action first and then **More actions**, a native disclosure.
  Under it, **Merge requirements**, with optional checks, earlier runs
  and the technical details folded away.
- **Every action is the existing route**, wired once: a move, a launch,
  a hold, **↑ open PR**, the completion sheet (which is itself the
  confirmation), **Check the remote**, the launch check, a cancel, or a
  view that opens a sheet and changes nothing. Costly actions arm on the
  first click or Enter/Space and fire on the second. An action whose
  handler belongs to a later card arrives disabled from the server and
  is drawn disabled with its reason.
- **A live update keeps the reader's place**: the open folds, the scroll
  position, a focused action and any unsent text survive the redraw that
  every state change makes, and opening any of it starts nothing.

### Reading it is free; two actions are explicit

Viewing a card, the dialog, `/api/state` or `/api/card/delivery` writes
no state file, fetches nothing, asks GitHub nothing and launches nothing
— the only processes it can cause are the read-only local git queries
the authorities already make. Those stay reads even in a repository
configured otherwise: every git read bench makes (`config.git_run`)
switches off textconv and external diff drivers, clean filters, the
fsmonitor hook, hooks, signature checks and git's optional index
writes, so an attribute or a config line in the application cannot turn
a look at a card into running its program. A merge, a push, a fetch or a
worktree bench was asked to make runs exactly as before. Two POSTs sit
beside it, each a person's click:

- **Check the remote** (`/api/card/delivery/check-remote`) asks the
  application remote whether `task/<stem>` and `phase/<stem>` exist
  (`ls-remote`, bounded by `BOARD_FETCH_TIMEOUT`) and fetches only those
  into remote-tracking refs. An absent ref is an answer, not an error. It
  creates no local branch and moves nothing; the next reading reports
  what it found.
- **The launch check** (`/api/card/delivery/launch-check`) runs the
  launch's own refusals (`agents.launch_refusals`) — the very steps
  **▸ start work** takes before its claim: the stage and the phase-card
  rule, one agent per card, the plan, the profile, the prompt template,
  and whether someone else holds the card — and says what would refuse.
  It may fetch as `/api/plan` does, claims nothing, starts nothing, and
  the launch asks again.

### The PR observation survives a restart

The PR poll now writes a small snapshot (`state/gates/<stem>.pr.json`:
head, state, whether it merged, when it was observed) at the moment it
updates its own state — never on a read. So a board that restarts still
knows a pull request was merged on GitHub before its first poll. A
finished merge & clean up leaves `state/gates/<stem>.delivered.json`, so
a card reopened afterwards says its earlier work was delivered rather
than reading as never started. Both are machine-local for the reason
everything else under `state/gates/` is.

### A pull request merged on GitHub is reconciled, not re-gated

When the PR was merged outside the board, the status is
`merged-elsewhere` — read before the review-stage requirement rows — and
the action is **Finish: clean up**: the existing merge & clean up path.
It asks the application remote whether the target already holds the
branch's commit, and when it does, nothing is merged again and no merge
rule is asked for — the gate guards a merge this board would make, and
there is none. Publication is still read off the remote, the base still
catches up, and cleanup still keeps anything uncommitted. A snapshot
that says merged while the remote does not hold the commit is gated
exactly as before. A stacked card stays a stacked card: it lands through
its PR into its prerequisite, and its primary action says so.

The merge speaks only for the commit it merged. When the card's branch
on this board holds commits the merged head does not — work returned or
reopened under the same card — the status is not `merged-elsewhere`:
that newer commit's requirements apply as they would to any review
card, and the sentence says the earlier merge does not deliver it.

### The cache is not authority

`/api/state`'s compact reading is memoised by a digest of every input it
reads — the branch tips, the policy revision, the evidence and gate
epochs, each card's own text and stage, the agent registry, completions
in flight and unpublished, the phase runner's pass and the PR snapshots
— plus a time bucket. A change to any of those is a different key, not a
stale answer, and the full per-card reading is never cached.

## Readiness: asking before paying

A plan says which checkout a run works in. A profile says what it may do
there and which files it is guaranteed. Verification says whether the
project's own checks passed and against what, and a gate says whether
that is enough to merge. Every one of those is resolved before a billable
model is invoked — and until this, there was no way to *ask* any of them
without invoking one.

So an operator found a mistyped policy the expensive way: move a card,
click **▸ start work**, watch the run refuse, read the ticker. A required
input that was not there, a check `review` could not ask for, a
`requires` naming a guarantee nothing enforces — each is a sentence bench
can produce in milliseconds, and each was only ever produced by a launch.

```bash
python3 .task-manager/manager/board.py --doctor              # inspect
python3 .task-manager/manager/board.py --doctor --help       # every option
python3 .task-manager/manager/board.py --doctor --all        # + the active stages
python3 .task-manager/manager/board.py --doctor --json       # for a script
```

and the same report behind the **⚕ configuration** chip in the header, in
**Settings → Diagnostics**, and in the Focus view beside **Permissions &
inputs** and **Verification**.

### What a person reads: the Configuration check and the launch check

The report is long because it is honest; the page is short because a
person has a next action to find. Two surfaces read it, and they answer
different questions.

**The Configuration check** is the board-wide one — the header's ⚕ chip
and Settings → Diagnostics draw the same result from the same renderer,
so there are no competing summaries. It leads with a specific result and
one sentence of scope, both written by the doctor (`summary.headline`,
`summary.scope`) and adapted to what was actually recorded:

- *No configuration issues found* and *Configuration inspected. Agent
  execution has not been tested.* for an inspected-only reading. Once a
  stage has run, the sentence says what it found instead (*Controlled
  execution: passed. The installed agent CLI has not been tested.*), and
  a policy document that does not parse reads *partially inspected*.
- Then the problems, each once (`summary.issues` folds the echoes of one
  refusal into one row): the purpose or setting it affects (the purposes
  are **Implementation**, **PR fixes**, **Review** and **Card
  refinement**, the names Settings → Agents uses), a state word naming
  its evidence level, the problem and its **Fix**.
- Everything that is fine folds into a count, and everything not tested,
  not enforced or not configured folds into a count of its own — never
  into the first. A tick is only ever a row's own, at its own level:
  `Configuration valid`, `Conformance passed`, `Execution passed`; and
  `Blocked`, `Unsupported`, `Invalid`, `Execution failed`, `Not tested`,
  `Not enforced`, `Not configured` for the rest. No overall pass is drawn,
  and the header chip never wears the calm colour for a static reading.
- A reading taken against a policy revision the board has since moved
  off reads `Stale`, and a check that could not be asked says it did not
  complete rather than showing an earlier answer.
- **Workspace locations** and **Execution permissions** fold away with
  short labels and one left-aligned path per row, each with a labelled
  **Copy**; **What was checked?** holds the definitions, what each stage
  proves and does not, every diagnostic and its remedy.
- The executing stages keep their own armed controls with their side
  effects beside them, and their own results. Reading or expanding any of
  this runs nothing.

**The launch check** is per card and per purpose — the card's ⚿ chip,
or **▸ launch check** in its tools on a board with no policy document. It
opens on the purpose the card's own action would launch (Implementation
in `in-progress/`, Review on a review card until a review is in, then PR
fixes) and reads `/api/plan` and `/api/profile`, the resolvers the launch
rides: where the work starts and what its PR targets (a stacked card shows
its own branches, never `main`), the harness and model (*harness default*
when none was chosen — never an invented name), the access, intended
writes beside *OS isolation: none enforced*, and anything that stops it
with its fix. For a review it says the run is read-only and which PR head
would be pinned. The next action is the card's own button — same key,
same arm-then-fire — or, when blocked, no button and the fix. Nothing the
sheet resolved is sent with the launch: the launch runs its own preflight
again, so a sheet opened before a branch or policy changed cannot carry a
launch past the refusal. Launch readiness is its own signal; merge rules,
Bench checks, CI and review stay on their own chips.

### One evaluator, read from four distances

The thing the doctor must not be is a second opinion. It owns no policy
logic: it calls `profiles.resolve()` for each of the four launch
intents and reports the `diagnostics` that came back, it calls
`verify.request()` to run a check, and it asks each adapter what *it*
would generate. Every code it reports is one `profiles.DIAGNOSTICS`
names. A doctor that could disagree with a launch would be worse than
none, so `profiles.seams()` says in as many words that it has no second
parser and must not grow one.

What it adds is **distance**, and saying honestly how far each answer is
from the thing a person actually cares about:

| level | what it proves | what it does not |
| --- | --- | --- |
| `static` | what bench resolved from the policy in force — roots, grants, readable inputs, checks, every refusal, exactly as a launch would | that any of it was carried out. No process ran |
| `generated` | what the installed adapter would hand its vendor: the argument list, the working directory, the allow and deny rules | that the vendor honours any of it. These are rules as text |
| `stub` | that the adapter's own `run` executed and passed bench's prompt, flags and working directory through | anything about the real CLI's permission behaviour. A stub answers; it does not enforce |
| `runtime` | that bench's own runner built the argv, started the process, waited on it and recorded the status it got | that the project's real suite passes. A fixture is a fixture |
| `real-agent` | that the installed CLI, with a model, was handed this policy and behaved as recorded | anything until it is separately authorised |

The four cheap levels cannot be read as the expensive one, and that is
enforced rather than left to a reader: `MODE_EVIDENCE` pins each mode to
one level and `report()` **raises** rather than emitting a finding that
claims another. Every row carries its level; every level carries its own
"does not prove", in the table above's words.

Four outcome words, everywhere, and the word carries the meaning rather
than the colour: `passed`, `failed`, `skipped` (nothing to ask — this
board has not configured the thing, which is not a fault) and `deferred`
(this needs an authorisation, or a guarantee nothing installed here
enforces). `deferred` is deliberately not `passed`.

### Four stages, and only the first is free

- **inspect** — resolves every intent, reads the approved inputs, and
  puts bench's own deliberately broken fixture documents through the same
  resolver. No process, no pin, no checkout, no token. This is what a GET
  of `/api/doctor` does, which is why opening the inspector is not an
  action and needs no confirmation.
- **conformance** (`--conformance`) — asks the installed adapter what it
  would generate for each intent, and runs its own `run` against a stub
  standing in for the vendor binary. Runs the adapter's own executables,
  in a temporary directory the doctor removes. No vendor binary, no
  model, no network.
- **controlled execution** (`--active`) — runs named checks through
  `verify.py` itself at a git fixture the doctor creates under
  `manager/local/state/doctor/` and removes. Three of them, because "it
  failed" is three different things to do next: one that exits zero, one
  that exits non-zero, and one whose required preparation will not
  complete. Real processes, real evidence records — filed under the
  doctor, never under a card — and no card moved, no branch touched,
  nothing published.
- **the real CLI** (`--real-agent --approve-real-agent`) — the one thing
  no cheap level can answer, in both directions. Two bounded launches at
  one disposable fixture, a short prompt and a timeout each, with this
  board's configured account and model untouched: bench passes no model
  of its own, so the CLI resolves whatever the operator's settings
  already say, and the launch identity (`BOARD_AGENT_ID`, `BOARD_TASK`)
  is removed so nothing reports against a card. The `work` probe is
  asked to write one file; the `review` probe is asked to attempt a
  write, a commit and a push. It takes two flags, and the API takes
  `"approve": true`, because nothing else should ever spend a token.

  **The verdict is read off disk, not off the log.** The run is asked to
  report what it was allowed to do, and bench ignores that and looks:
  is the file there, did the fixture gain a commit. A model cannot
  attest to its own confinement, which is the same rule `verify` applies
  to a check's exit status. One row stays `deferred` however much is
  authorised — whether the CLI is *prevented* from reading a file beside
  its approved inputs, because nothing prevents it and no launch could
  demonstrate otherwise.

Without that approval every row is still there and reads `deferred` — a
check that disappears when nobody runs it reads as one that passed. And
the coverage table draws every stage nobody asked for as `not asked`,
for the same reason: a stage missing from a coverage table is a stage a
reader assumes was fine.

### The diagnostics, and their remedies

Every refusal `profiles.resolve()` can produce now carries the **code**
it came from and a remedy for the class of it, named once in
`profiles.DIAGNOSTICS`. `blockers` is unchanged and still prose — it is
what the page leads with and what `require()` raises — and `diagnostics`
sits beside it, so nothing downstream has to match on a sentence that
could be reworded.

Bench ships its own broken documents to prove the refusals still work,
and the doctor resolves them through the real resolver so an operator
reads the exact sentence their own mistake would produce. Five of them,
each a different thing to go and fix:

| fixture | code | what you would see |
| --- | --- | --- |
| a required input that is not there | `context-unreadable` | *the context input 'workflow-brief' … is not there. Put the file where the selection names it, inside the root it names, or mark the selection `"required": false`* |
| a review told to verify with something only `work` holds | `check-undeclared` | *'review' asks for the check 'unit', which this document declares neither as a check nor as one of this intent's command grants* |
| a check declared against the application checkout | `check-root-unsafe` | *the check 'unit' names the execution root 'app' — one of worktree, source, review. A check never runs in the application checkout: that is the live code and the live services* |
| `requires: ["filesystem-sandbox"]` | `requirement-unsupported` | *nothing installed here enforces it, so the launch is refused rather than run under a restriction that exists only in the prompt* |
| a check needing a preparation step nothing declares | `prepare-undeclared` | *add it to the document's top-level `"prepare"` list, or drop the reference; bench will not run a step it cannot read* |

And the sixth, which the runner rather than the resolver answers: a
**required preparation step that fails** is recorded `prepare-failed`
with the check never run — a dependency that would not install is not a
test that failed, and recording it as one sends a reader to the wrong
file. The `--active` stage proves that distinction on every run.

A diagnostic blocks the intent it is about and no other, because
readiness is per launch rather than per board: a `work` profile missing
its brief does not stop a review.

### What it says about where things are, and what it withholds

A diagnosis names **exact operator-local paths**, on purpose: "the input
is not there" is useless without saying where bench looked, and the
report never leaves the machine that made it — it reads `local to this
board`, and nothing in it is committed, pushed or carried into a pull
request. What it never carries is a *value*. No environment variable's
content, no credential and no approved file's contents are read into it
at all (the profile layer's allowlist has none to send), and the one
place bytes bench did not write get through — a check's own output — is
scrubbed by the same scrubber the evidence log uses. The report says all
of this in a `redaction` block rather than leaving a reader to wonder.

### Adapter conformance, without core learning a vendor

Checking that an adapter's translation is faithful means reading rules in
the vendor's own syntax, and core knows nothing about any vendor. So the
adapter answers for itself, through two things it declares in
`capabilities.json` — a `describe` command that prints what it *would*
hand its vendor for one launch in a normalised shape, and the name of the
environment variable that replaces the vendor binary so its `run` can be
exercised against a stub. The whole contract is in
`manager/core/adapters/README.md`.

From those the doctor asks, per intent: did every named grant become a
rule with its fixed arguments intact, is the working directory the one
bench named, do the pinned inputs reach the run as paths, can this intent
ask bench for a named check (`review` included), was the read-only intent
granted any way to write, and does any rule reach `git worktree`,
`reset`, `checkout`, `switch`, `branch`, `update-ref` or `remote` —
which would let a run choose a different lifecycle than the one bench
planned and recorded.

One row is always `deferred`, and it is the honest one: **nothing
prevents a run reading files beside its approved inputs.** Both shipped
adapters declare `filesystem-sandbox` unsupported, bench enforces no such
boundary, and the pinned bundle is a guaranteed *input* rather than a
limit on reading. A profile that *requires* it is refused rather than
reported as honoured, and that is the only honest answer available.

An adapter that declares neither seam is not accused of anything: its
rows read `skipped`, naming what it would have to declare. What they
never read is `passed`.

### A portable setup, end to end

Everything above works on a board that configured nothing — a legacy
board has no document, so `inspect` reports the legacy grants and the
diagnostics table, and that is a real answer. What the doctor is *for*
is the board that configured something. A separate-root install
(`workspace/app`, `workspace/bench`, `workspace/work`) with selected
workflow files, a scoped check and a preparation step, in
`manager/local/profiles.json`:

```jsonc
{
  "v": 1,
  // selected from the tool and work-state roots by word, never by path:
  // the document is machine-local but it is still read on a machine
  // whose paths are its own
  "context": [
    {"name": "workflow-brief", "path": "tool:manager/local/AGENTS.md"},
    {"name": "design-system", "path": "work:reference/design-system.md",
     "required": false}
  ],
  "prepare": [
    {"name": "deps", "run": "python3 -m pip install -e .", "network": true}
  ],
  "checks": [
    {"name": "unit", "run": "python3 -m unittest discover -s tests",
     "root": "worktree", "prepare": ["deps"], "timeout": 900,
     // a scope is a *declared name*: the arguments are the operator's,
     // never the run's
     "scopes": {"fast": ["-k", "fast"]}},
    {"name": "proposed", "run": "python3 -m unittest discover -s tests",
     // the checkout bench pinned for a PR review, detached at the
     // reviewed head — so a review's check is about the proposed code
     "root": "review"}
  ],
  "profiles": {
    "work":   {"verify": ["unit"]},
    "act-pr": {"verify": ["unit"]},
    "review": {"verify": ["proposed"]}
  }
}
```

Then `--doctor` says, before anything is launched, whether both briefs
are readable, whether each intent can ask for its check, that `deps`
needs the network and nothing here bounds that, and — with `--active` —
that bench's runner really does tell a failing test from a dependency
that would not install. The `--real-agent` row stays `deferred` until
somebody authorises it.

Two things the doctor will not do, and they are both deliberate. It will
not fix your configuration: every failing row carries a remedy and
changes nothing. And it will not validate anything but its own fixtures —
the application's real suite, its services and its data are never what a
diagnosis runs against.

## Settings: reading the configuration, and changing it safely

Everything above is configuration an operator sets up. Until this, the
only way to *read* it was a JSON document and a terminal, and the only
way to change it was a text editor. The **Settings** view — the fifth,
beside Board, Phases, Sessions and Focus — is where it is read instead:
**how is this bench configured, what has actually been verified, and what
should I configure next?**

It owns no policy. Every value in it comes from the authority that
already answers for that question — `profiles.resolve()` for what a run
may do and read, `profiles.gate_policy()` for what a merge must have,
`verify`'s own records for what the project's checks found,
`gates.evaluate()` for whether a merge may happen, `config` for the roots
and each setting's provenance. There is deliberately no second reader of
the profile document: a draft is parsed by the same function a launch is
(`profiles.document_from`, which is `document_at` minus the disk read)
and resolved by the same reading the readiness inspector takes, so a
field error on the page is the diagnostic a launch would produce, with
the same code.

Opening it is a GET. Assembling the page resolves the policy, reads
records already on disk and asks git for the tool's own revision. It runs
no project program, invokes no adapter hook, launches no model, pins
nothing and writes nothing — which is why Settings is a view and not an
action.

### Eight words, so that nothing reads as a pass

The one word this page exists to refuse is a ninth covering all of them.
Each of these is a different thing to do next, so each gets a word, and
**the word carries the meaning** — colour never stands alone:

| state | what it licenses you to believe |
| --- | --- |
| `not-configured` | nobody configured this. Not a fault, and **not a pass**: nothing is required and nothing is proved |
| `configured-untested` | what it would do is resolved; that it does it is not established |
| `unsupported` | nothing installed here can deliver it, so bench reports it rather than promising it |
| `invalid` | the configuration declares it and bench refuses it — with the code and the remedy |
| `blocked` | it could not be asked: a permission, a dependency or a capability was missing before anything ran |
| `failed` | it ran and said no |
| `stale` | it passed, and what it was about has changed since |
| `verified` | bench itself ran it and recorded a pass bound to the exact source it ran against |

So "no gate configured" can never render as a passed gate, a registered
check is drawn as *registered, not required*, and a guarantee nothing
enforces wears `unsupported` rather than a switch that pretends.

### Eight sections, values first

The sections sit in a **sidebar** on the left, in three groups —
**Configure** (Overview, Setup guide, Checks, Merge rules, Agents, Agent
access — the Setup guide has a section of its own below), **Inspect**
(Diagnostics, Installation) and **Advanced** (Advanced policy) — with the
current one marked by a bar, a heavier weight and `aria-current`, so none
of the three is carried by colour alone. Each section opens with its own
title and one sentence saying what it is for, and its content is a column
of white panels on a pale grey-blue ground in Daylight (the night theme
draws the same layout in the board's own tokens). Below 760px the sidebar
folds into one **Section: …** button that opens the same list in place
and closes once you choose; nothing is squeezed into a narrow column.
Actions come in three registers: a solid button for the section's primary
action (**Add a check**, **Check configuration**, an enabled **Apply**),
outlined buttons for the rest, and underlined text links for going to
another section. State badges are not buttons. `Optional` and `Not
inspected` are neutral, and an absence that changes what a merge means —
`No merge requirements`, an `ungated` rule, OS isolation `Unavailable` —
is amber, with the words always there. All of it is scoped to the
Settings view: Board, Phases, Sessions and Focus are drawn exactly as
before.

Every section leads with the same row: what the setting is, its
effective value in words, at most one short helper line, its state word,
and one action that goes somewhere specific: **Edit checks**, **Edit
merge rules**, **View access**, **Check configuration**. A problem is drawn
under the row it is about. Tutorials, ownership tables, capability
inventories, worked examples and definitions are in closed folds under
each section, one click away, so they are not the first thing you read.
Two limitations stay in view because they change what a merge or a run
means: a board with no merge requirements says so, and agent writes are
an intended scope, with OS isolation `Unavailable`.

- **Overview** — three panels (verification and merging, agent runs, and
  this board), with one row each for checks (`1 registered · Optional`, or
  `None registered`), merge rules (`No merge requirements`, or what each
  rule requires, or `Invalid`), reviews, overrides, agent access (`3
  profiles: work, act-pr, review`), OS isolation, diagnostics (`Not
  inspected` until you ask), the installation and the policy document.
  Under them, **Suggested next steps** opens on its own only when one is
  high priority. Each suggestion says why it matters, what *you* must
  supply (often a program your team writes, not a setting), what has to
  be true first, and which section to open. A suggestion does not change
  any setting.
- **Checks** — every registered check as its configuration, never as a
  result: its name and the purpose its `help` records, then where it runs,
  its preparation, its time limit and which launches may ask for it, then
  its full command, with the executable, arguments and identity folded
  under it. Beside the name, `Optional` or the merge rule that requires
  it, and an **Edit** button. Preparation steps have a panel of their own,
  which says so when none is declared. What a check *found* is per card,
  in its `◫` evidence. **Add a check** and **Edit** open
  the editor in place. Who provides checks (**"Your team provides the
  checks; Bench runs them."**), where a check runs and what that location
  is not (a checkout is not a sandbox), and a worked example with a
  checklist for asking a developer are all folded underneath.
- **Merge rules** — the three guarded merges as rows: a phase member into
  its phase branch, a phase's acceptance, and a card's merge. Each row
  shows what it requires and its review policy, reads `gated` or
  `ungated`, and has an **Edit** button that opens it in place. Overrides
  get a row too. Beside them, **Three separate signals** keeps Bench-run
  checks, external CI and review requirements apart: green CI never
  counts as a named Bench check. Review policies (including the
  same-account limitation and the ones this board cannot read), how
  evidence is bound to the merging commit, and what an override records
  are folded underneath.
- **Agents** — which coding-agent harness and model each kind of run
  rides, and the prompt each is given. See "Agents: harness, model and
  prompt per purpose" below; it has a draft and a step bar of its own.
- **Agent access** — one row per launch intent: where it works, its
  grants, the checks it may ask for and its approved inputs, with
  `intended writes: … · OS isolation: none enforced` on its own line. Runs
  in flight are shown with the policy they *froze* at launch, which an
  edit here does not reach. The four facts an exit code cannot tell apart
  and the capability table are folded underneath.
- **Diagnostics** — three panels, because they are three different
  things. **Check configuration** is the read-only inspection
  (`doctor.report([])`) and its findings. **Execution diagnostics** are the
  stages that run real processes, each armed, with its side effects next
  to its control. **Run from a terminal** holds the stage that spends
  tokens, which has **no control on this page** at all; the operator
  command is printed instead. Every stage nobody asked for reads `not
  asked`.
- **Installation** — a summary first: the build and source revision
  (`unknown` for a release artifact, which has no commit), the mode,
  both git identities and sync. Then every catalogued setting with its
  value, its source (`default`, `local .env` or `environment`, with
  `locked` where the environment wins) and a restart chip only where it
  differs from the rest. Where values come from, and when a change
  applies, is said once above the list, not on every row. Paths, what stays
  local, and the operations this page does **not** perform are folded
  underneath.
- **Advanced policy** — the whole document, field by field and as JSON:
  checks, preparation steps, what each launch may ask for, merge rules
  and overrides.

### Editing in context, on one draft

A check is edited in **Checks** and a merge rule in **Merge rules**,
where you read them. There is still exactly one mechanism. The
contextual editors and **Advanced policy** all write into the same
draft and use the same step bar (Validate, Preview, Apply, Discard
draft), so a change started in one place and finished in another is
validated, previewed and applied once. A refusal about the item being
edited is drawn at its fields. Every other refusal in the draft is drawn
in the step bar, so none is hidden because its part of the document is
not on screen. **Close** puts the editor away, keeps the draft and
returns focus to the **Edit** button it came from. While a draft has
unsaved edits, every other section shows **Unsaved draft** with the way
back to it and the way to discard it. Ticking a check in a merge rule
adds its full run; a requirement on a declared scope that someone wrote
by hand stays as written.

### What is editable, and what is shown instead

One file: the execution policy document (`manager/local/profiles.json`,
or wherever `BOARD_AGENT_PROFILES` points). That is where checks,
preparation steps, approved context, command grants and gates live, and
it is read fresh on every launch — so a change applies to the next launch
and never to a run already in flight.

`manager/local/.env` is **read-only here**, and the page says why rather
than offering a control. `config` snapshots that file at import, so a
value written from the page would not be in force until a restart; and a
key the real environment also sets would not be in force at all. A
control that loses to a higher-precedence source is worse than no
control, so each row names the source that won and how to change it
there.

The one exception is the agent keys — `BOARD_AGENT_ADAPTER` and its
per-purpose overrides, `BOARD_AGENT_MODEL` and its — which every launch
reads fresh, and which **Settings → Agents** edits (next section). In
**Installation** they carry a `next launch` chip and an **Edit in
Agents** link instead of a restart chip; one the environment sets is
still `locked`.

### Agents: harness, model and prompt per purpose

**Settings → Agents** answers which coding-agent harness each kind of
run goes through, which model it asks for, and what it is told — and the
answer it shows is the one the next launch rides, because it is read off
the same resolver.

**One resolver.** `config.agent_choice(intent)` reads the agent keys on
every call — the process environment first (locked: change it where it
is exported and restart), then `manager/local/.env` as it is on disk
now, then the documented default. The preflight, `profiles.resolve()`
and therefore every manifest, the doctor and this page all ask it, and
`_launch` takes the harness and model out of the manifest the preflight
validated rather than asking again. That is the only change to `.env`
semantics: every other key is still read once at start-up.

**The rows.** A compact **Default** row (harness and model) and three
purpose rows — **Implementation** (`work`), **PR fixes** (`act-pr`) and
**Reviews** (`review`, which covers both PR reviews and task relevance
checks). Each purpose offers **Inherit default (…)** explicitly, naming
what it would inherit, or an override; the default model's empty value
means **harness default** — no `AGENT_MODEL` is passed and the CLI
resolves its own, and a run's record says so rather than naming a model
nobody chose. Card refinement rides the default harness and is shown,
read-only, beside them. A board that sets none of the new
`BOARD_AGENT_ADAPTER_WORK` / `_ACT_PR` / `_REVIEW` keys resolves exactly
as the global settings always did.

- **Harnesses** are the installed adapter directories, local before core
  (`local/adapters/<name>` shadows `core/adapters/<name>`); each is shown
  with the setup note its `capabilities.json` declares. An uninstalled
  one is drawn as `not installed` with the remedy, refused by Validate,
  and — if one is configured anyway — refuses the launch in preflight
  with `harness-unavailable`, before a claim, a branch, a worktree or a
  review checkout exists. Choosing a harness installs nothing, changes
  no account, rewires no hook and starts nothing. Live-session hooks for
  the operator's own sessions still follow the default harness (`wire`);
  a purpose on opencode needs opencode's plugin wired as its setup note
  says.
- **Models** are opaque vendor identifiers, passed through untranslated.
  There is no catalogue and no availability claim; a value is refused
  only if it could not be one token on one `.env` line, or looks like a
  credential. When a purpose changes harness but still inherits a model
  chosen for another, the row and the preview say so.
- **Permissions are separate.** Capabilities and the execution profile
  are resolved for the harness the intent actually rides: a harness that
  does not declare what a profile requires refuses the launch exactly as
  before, and nothing here widens a grant.

**Prompt templates.** The four templates — Implementation (`work.md`),
PR fixes (`act-pr.md`), Task relevance review (`review.md`) and PR
review (`review-pr.md`) — are listed with their source (**Built-in** or
**Local override**), their placeholders and which are required. **Edit**
opens the text in place; **Render preview** fills it with explicit sample
values or a card you pick (its body inserted as data — a `{branch}` in a
card stays those characters); **Diff from built-in**, **Reset to
built-in** (staged, previewed, then applied) and **Cancel changes** sit
beside it. Only `manager/local/prompts/<name>` is ever written, from a
fixed list of four names; nothing under `core/` is touched.

Each template is held to a contract (`templates.py`) before it is saved
**and** before any launch uses it: the text must parse as a format
string, every placeholder must be one that launch fills (named plainly —
no `{body.x}`, `{0}`, conversion or format spec), the inputs the runner
depends on must be present (the task, the workflow brief, the plan, the
pinned context, the verification and progress blocks), and the marker
the board parses (`NOT READY:`, `ADDRESSED:`, `RELEVANCE REVIEW:`,
`PR REVIEW:`) must still be asked for. An existing local override that
fails is shown with its problems and its text, never rewritten, and
refuses the launch that would use it until it is fixed or reset. Prompt
text is instruction, not permission: the profile, context and
verification blocks bench inserts are required in it.

**What a run keeps.** Each launch records, in its manifest, the harness
and model selection with where each came from (`selection`) and the
template's name, source and hash (`template`) — never its text — beside
the profile it rode. A later edit changes the next launch only; the
Focus view's freshness note says when the settings have moved on.

**Saving.** The same four steps as the policy editor, through
`/api/settings/agents/{validate,preview,render,apply}` behind the same
origin door. Preview compares two resolutions per purpose — harness,
model, inheritance, the capabilities the new harness lacks — and shows
each template change as a diff. Apply is armed, refused on a stale
revision (a **conflict**, not a last-writer-win; an unrelated `.env`
edit is not a conflict and is preserved), writes only the agent keys
that change — every other line and comment byte for byte — and only the
named templates. Every file it is about to touch is backed up first under
`manager/local/state/settings/agents-recovery/`; a write that fails part
way puts back what it had replaced, and the kept backup is the
**Recover replaced text** list under each template. One sanitized line
per apply goes to `state/settings/agents-history.jsonl`: key and template
names and hashes, never a value. Nothing is launched, tested, restarted
or moved by any of it.

### Four steps, and only the last one writes

1. **Edit** the draft. Nothing is written, and leaving the page discards
   it.
2. **Validate** — the draft through bench's own parser and resolver, with
   each refusal's code and the field it is about. Writes nothing.
3. **Preview** — two resolutions compared, never a prediction: what the
   requirements become, what a run may do, which recorded passes stop
   counting (changing a check's definition changes its identity, so every
   pass of it reads `stale` — it is not deleted and it is not re-run; an
   edit elsewhere in the document leaves a pass with a result identity
   standing, and only legacy records read `policy-changed`), and
   which cards waiting in `review/` would change their merge decision.
   Writes nothing.
4. **Apply** — armed, and refused rather than repaired. An invalid
   document is never written; a revision that moved under the editor is a
   **conflict** naming the current one, not a last-writer-win; unknown
   top-level keys from the file on disk are preserved; the write is an
   `os.replace` of a temporary file in the same directory, so a failed
   write leaves the previous document byte for byte.

A setup, end to end, for a project whose team already has a test
command. Open **Settings → Checks**, unfold who owns what, then **Add a
check** and register the command as a named check — which, written out
as the document **Advanced policy** shows, is:

```jsonc
{
  "v": 1,
  "checks": [{"name": "unit",
              "run": "python3 -m unittest discover -s tests",
              "root": "worktree",
              "scopes": {"fast": ["-k", "fast"]},
              "timeout": 900}],
  "profiles": {"work":   {"verify": ["unit"]},
               "act-pr": {"verify": ["unit"]},
               "review": {"verify": ["unit"]}}
}
```

**Validate**, **Preview**, **Apply**. Nothing is mandatory yet: every gate
still reads `ungated`, and the check's row says `Optional`. Exercise it —
ask for it from a card's `◫` evidence sheet, and see it fail once on
purpose — and only then, as a separate decision, add the gate with
**Merge rules → Card merge → Edit**:

```jsonc
"gates": {"acceptance": {"checks": ["unit"]}}
```

A sanitized line goes into `manager/local/state/settings/history.jsonl`
per applied change: the time, the revisions, the keys that changed, and
the actor **where one is actually known**. This board records no
authenticated identity, so an unknown actor is written down as unknown —
a forged attribution is worse than an absent one. No values: a settings
file can hold a path out of somebody's home directory.

**And applying cannot move work.** It writes one file. It starts no
agent, moves no card, merges nothing, publishes nothing, re-runs no check
and restarts nothing; a gate takes effect the next time the mutation it
guards is attempted, not because a file was saved. A run already in
flight keeps the manifest it launched with, because that manifest is a
record on disk and nothing here touches it.

### Writing a policy is an authority boundary

The board binds to loopback and has no authentication, and that is the
whole threat model: a page on another origin, opened in the same browser,
can post to `http://127.0.0.1:26071` without ever reading a reply. So the
one route on this server that edits policy is held to a stricter door than
the rest, in the server rather than in the page, and none of it trusts a
field in the body — a JSON content type (so a cross-origin form, which
may only send form encodings, is not a request this parses), a custom
header (which a form cannot set at all, and a cross-origin `fetch` can
only set by asking for a CORS preflight that nothing here answers), and
an `Origin`/`Sec-Fetch-Site` check for the browsers that send them. A
loopback client that is not a browser sends the header and is allowed,
which is correct: it already has the operator's own filesystem access.

Check registration is not a file-write or shell endpoint either. A
compound command, a pipeline, a redirection, a substitution, a relative
executable, a path climbing out, the application checkout as an execution
root and an argument naming a credential are each refused where the
document is read — by `profiles`, the same parser that refuses them for a
launch. Secrets are not managed here: credential-shaped names and values
are masked on the way out, and a submitted mask is refused rather than
written back over the value it hid.

## The setup guide: one proposed configuration, resumable

`bootstrap.py` is the terminal half of first-time setup; it ends by
naming the board's URL with `#setup` on it. That opens **Settings →
Setup guide**, the browser half — and `board.py --setup-guide` prints the
same reading in a terminal, for a person or their coding agent. It is
not a second configuration store and owns no setting: every fact in it is
asked of the module that already answers for it (`config` for the layout,
the remotes, `gh` and the driver; `config.agent_choice` for the harness
and model; `settings.verification` and `profiles.gate_policy` for checks
and merge rules; `verify.outcome_now` for what a check last found), so the
guide cannot disagree with a launch.

### Six areas, each with a next action and its owner

**Workspace and task storage**, **Coding agent**, **Project
instructions**, **Verification**, **Application preview** and **Review
and delivery**. Each shows what was discovered, what bench recommends and
why, what follows from it, and one next action naming who holds it:
**you** (a login, an install, an approval, a decision), **an agent,
through a card** (engineering work — instructions, a driver, test
programs), or **Settings** (a value bench already edits safely, linked
rather than duplicated). The agent area recommends the harness default
and the CLI's own model, and lists every installed harness with whether
its CLI is on PATH; nothing forces the advanced choices.

Project instructions the application already has (`AGENTS.md`,
`CLAUDE.md`, `.github/copilot-instructions.md`) are authoritative and
never edited. When there are none, the guide proposes a **setup card**
in `backlog/` asking for instructions that describe the project's real
conventions — it never writes boilerplate into the application.

### Eight words, and no percentage

| status | what it licenses you to believe |
| --- | --- |
| `not-inspected` | nothing has looked, or the reading could not be taken |
| `decision-needed` | bench found what is here and cannot choose for you |
| `configured` | set up, and not exercised. Configured is not tested |
| `tested` | bench ran it and recorded a pass that still holds |
| `failed` | it ran and said no |
| `blocked` | something outside bench must change first: a tool, a login, a layout bench refuses |
| `deferred` | someone chose to come back later, with a reason |
| `not-applicable` | someone recorded this project does not need it, with a reason |

The summary is a count per word. A percentage would read "mostly set up"
about a board whose one missing piece is the one that matters, so there
is none. The real CLI under bench's policy reads `not-inspected` until
the doctor's `--real-agent` stage is deliberately run.

### The contract (v1)

`GET /api/setup` returns `{"setup": {...}}` with `kind:
"bench-setup-state"`, `v: 1`, the `revision` of the shared record
(`"none"` before the first write), `readOnly` (why writes are refused,
or null), `statuses`, `owners`, the six `areas` (each `id`, `status` as
shown, `computed` as read now, `facts`, `recommendation`, `consequence`,
`next` with `owner`/`kind`/`label` and a `section`, `command` or
`detail`, `fingerprint`, `decision` with `stale`, the linked `task`, and
`allowed` mutations), `summary` (`counts`, `open`, `stale`, `invite`),
the bootstrap `handoff` read as history, and `interrupted` operations.
Reading runs no project program, launches no agent, repairs nothing and
writes nothing.

Two mutations, behind the same door as a policy write (JSON content
type, `X-Bench-Settings: 1`, origin checks):

- `POST /api/setup/decide` — `{area, decision: defer | not-applicable |
  reopen, reason, expected, request}`. A reason is required (3–500
  characters, scrubbed of anything credential-shaped); workspace and
  agent cannot be not-applicable.
- `POST /api/setup/task` — `{area, expected, request}` for
  instructions, verification or preview: writes one ordinary card to
  `backlog/` and links it. Nothing starts.

`expected` is the revision the client read; a different one is `409`
with `stale: true` and `current`, and nothing is written. `request` is
the client's identity for this attempt: the same one again answers with
the first result (`duplicate: true`) rather than acting twice, and one
whose operation was interrupted *resumes* it — a card already written is
adopted, not written again. The CLI takes `--decide AREA DECISION
--reason R`, `--create-task AREA`, `--expected` and `--request`, and
calls the same functions. Configuring a check, running one and enabling
a merge rule stay in Settings → Checks, a card's ◫ sheet and Settings →
Merge rules, with their own protections.

### Who owns what is stored where

| Path | Holds | Shared |
| --- | --- | --- |
| `<work root>/setup/decisions.json` | per area: decision, reason, actor, time, the fingerprint of the portable facts it was made against, and the linked setup card | yes — committed like any board edit under `BOARD_COMMIT_MOVES` |
| `manager/local/state/setup/operations.json` | the operation journal: request identities, interrupted work | no — machine-local |
| `manager/local/state/bootstrap/handoff.json` | what the terminal setup left (task 87) | no — machine-local |

The fingerprint is a hash of words and names (layout mode, which
instruction files exist, which checks are registered), never of a path,
so a teammate's board reads the same decision and the same staleness. A
decision whose facts changed reads `stale` and asks to be revisited;
nothing is reset. Unknown top-level keys and unknown fields of a record
survive every write; a document from a newer bench, or one that does not
parse, is shown read-only and never written over.

### First visit, and existing boards

A **◇ set up** chip in the header appears while an area needs a decision,
is blocked or failed, and opens the guide. It is an invitation, never a
gate: the board works normally whether or not anyone opens it, and an
existing installation opts in simply by reading it — nothing is reset,
and an area already configured reads `configured`, not `tested`.
Deferred and not-applicable areas stay revisitable with **Reopen**.

### Setup cards from discovered gaps

The guide's **Proposed setup cards** panel turns what inspection actually
found into ordinary Bench cards (`setuptasks.py`). Inspection reads the
application's files — `package.json`, a `Makefile`, `pyproject.toml`,
compose files, fixture directories, browser test configuration, the
instruction files — and the policy and driver bench already knows about.
It runs nothing. Five gaps are known, each with a stable identity:

| gap | proposed when | not proposed when |
| --- | --- | --- |
| `instructions` | no `AGENTS.md`, `CLAUDE.md` or Copilot instructions | one exists |
| `fixtures` | service or data configuration, and no fixture directory or preparation step | either exists, or nothing needs one |
| `preview` | a way to run the app (a `dev`/`start` script, a run target, `manage.py`), and no driver | a runnable driver exists, or nothing runs |
| `ui-smoke` | a user interface, and no browser test configuration or dependency | a Playwright or Cypress setup exists, or there is no UI |
| `verification` | no named check is registered | a check is registered |

Each gap says why, lists every file and setting it read with its finding
(`present`, `absent` or `unknown`), and names its deliverables,
acceptance criteria and the authority a run would need. **Unknown is not
absent.** A file that exists and cannot be read (`package.json` that does
not parse, say) makes the gap `unknown` and a blocker: it cannot be
created until someone can read the file, because a card built on an
unread file asks an agent to guess. A missing grant is stated, and bench
does not add it. For example, `BOARD_AGENT_COMMANDS` empty means a run can
edit but not run the project's commands.

**Prerequisites are lifecycle metadata, and only real ones.** Disposable
data comes before the startup that uses it, and startup comes before the
browser tests that drive it. So `preview` comes after `fixtures`,
`ui-smoke` after both, and `verification` after `fixtures`. Each is
written as a `**Depends on:**` line naming card numbers, and only when the
prerequisite is itself selected or already on the board. A prerequisite
that is neither is a warning in the preview, never an invented number.
Registering the delivered programs as named checks and choosing merge
requirements are your steps in Settings. They are listed after the cards,
with their owner, rather than written as cards an agent could not finish.

**Select, edit, preview, create.** Each gap can be selected, retitled,
re-prioritised, given notes (no headings, header fields or markers the
board parses), deferred with a reason, or pointed at an existing card to
reuse instead. A card whose head already carries `**Setup gap:** <id>` is
recognised as that gap's card, and cards whose titles look related are
offered. **Preview** (`POST /api/setup/cards/preview`) returns the exact
files, numbers and text and writes nothing. **Create**
(`POST /api/setup/cards/create`) is armed. It carries the preview's
`digest`, the record's `expected` revision and a `request` identity. A
preview that no longer matches, or a stale revision, is a `409` with
nothing written. A replayed request answers with its first result. An
interrupted one resumes and adopts the cards it already wrote. A gap
already held by a card is refused rather than doubled. Under
`BOARD_COMMIT_MOVES` the cards and `setup/decisions.json` land in one
`board: setup cards 08, 09 proposed …` commit, published by sync like any
move.

**Creating starts nothing**: no agent, worktree, phase, pull request or
application change. **▸ Start first task** (`POST /api/setup/cards/start`)
is a separate armed action. It runs the normal **▸ start work** path. A
dependency not in `done/`, an agent CLI that is not there, or a plan,
profile or template preflight that refuses each stop it before anything
moves. The card moves to `in-progress/` only once those pass, and if the
launch still refuses, the move is walked back.

**Outcomes link back, and are not proof.** Each gap shows its card's
outcome: `open`, `in progress`, `run failed`, `in review`, `delivered`,
`archived`, `missing` or `superseded` (re-inspection no longer finds the
gap). An archived or missing card lets the gap be proposed again, and
the new link records the card it replaces. `delivered` is delivery
metadata. If re-inspection still finds the gap, the guide says the merge
did not make the capability. If it no longer does, the area still reads
`configured` until bench's own check run passes. A card moving never
writes a setup status and never enables a gate.

The cards are portable. References are application-relative paths and
setting names, never a path from this disk, and notes are scrubbed of
anything credential-shaped. In `setup/decisions.json`, `tasks` maps each
gap to its card (`source: created | reused`, and `replaces` when it
follows an earlier card), and `gaps` holds per-gap deferrals. The
request journal stays machine-local, as before.

### Verification readiness: from a discovered test to a required check

Between "a test command exists somewhere in this repository" and "a merge
waits for it" there are five separate acts, and the guide's
**Verification readiness** panel (`readiness.py`) keeps every one of them
a separate, explicit action with its own status:

| act | what it does | writes | runs |
| --- | --- | --- | --- |
| **Discover** | reads `package.json` scripts, a `Makefile`, `Cargo.toml`, `go.mod`, a Python test setup, maintained entrypoints such as `verify/*`, `.github/workflows/*` `run:` steps and the commands in a README | nothing | nothing |
| **Register** | turns one candidate, or an explicit command a setup card delivered, into a draft of the policy document, validated and previewed by Settings' own `validate` and `preview` | the policy, only on Settings' armed **Apply** | nothing |
| **Run** | a card's `◫` evidence sheet asks `verify.request`; preparation runs first, as part of the same request | an evidence record | the approved check |
| **Review** | a person records that they read the program, a current pass and a controlled failure | `setup/decisions.json` | nothing |
| **Activate** | a merge-rule draft, offered only for a *ready* check, previewed with its consequences | the policy, only on Settings' armed **Apply** | nothing |

**A candidate is an inference.** Each one names where it was found (file
and line), a confidence — about *where the command came from*, never
about what it covers — and its prerequisites, each as the consent
boundary it is: a preparation step that downloads (`npm ci`, `pip
install`) is marked as needing the network and is refused from a draft
without that consent; services or data name the disposable-fixture gap;
an `.env.example` says bench supplies no secrets and tests must never be
given production ones. A compound shell line, or a program named by a
relative path, cannot be a named check, and the candidate says so: the
remedy is a wrapper program delivered through a verification setup card,
registered as `<interpreter> <path>`. Nothing is marked `executed`, and
every candidate's coverage reads `unproven`.

**Registering requires nothing.** A registration draft never touches the
`gates` block — the preview shows no `gate-changed` — so a registered
check is optional and runs only when someone asks for it.

**Readiness is a reviewed record, never an exit code.** A check's
readiness sits beside its last run and is never folded into it:

| readiness | what it licenses you to believe |
| --- | --- |
| `candidate` | inferred from a file; not registered, never run, coverage unproven |
| `registered` | declared; bench has no run of it as now defined |
| `exercised` | bench ran it and it passed; nobody has reviewed it yet |
| `attention` | the newest run did not pass |
| `review-stale` | it was reviewed, and its definition or program has changed since |
| `ready` | a person reviewed the program, a current pass and a controlled failure (or why one is not appropriate), bound to the check as it is now defined |

A review (`POST /api/setup/verification/review`) is refused, with
nothing written, unless the cited pass is card-bound evidence of this
check's *current* definition that `verify.outcome_now` still reads
`passed`, over a bound and clean source. The cited failure must be a
`failed` verdict of the same definition — a preparation failure, a
denial, a cancellation or an error reached no verdict and is refused as
one. Without a failing run, the reviewer says why a failing case is not
appropriate, and that reason is recorded. The record holds the check's
identity, the program identity bench resolved, the two commits, the note
and who reviewed it. It holds no path and no evidence log, and it is
portable, under `verification` in `setup/decisions.json`. A reviewer who
is also the verification setup card's assignee is recorded as not
independent, in those words. A setup card in `done/`, an agent's "tests
pass" and an approving line in a report change none of this.

**The bootstrap condition.** Until one check is ready, the panel, the
Verification area and `board.py --setup-guide --verification` all say:
*Automated verification is incomplete. Setup delivery needs human
review.* It is a statement, not an exemption. Merge rules already in
force are listed as preserved, and none is relaxed, skipped or
overridden. A setup card's run uses the ordinary work profile with no
extra grant. A rule that requires a check which is not yet ready is
named as the circularity it is: the setup card delivering that check is
held by the same rule. There are two ways through, and both are a
person's. The rule is satisfied by evidence on the setup card's own
commit, so you can run the check against the card's own worktree. Or,
where the policy enables one, you can take the recorded override, which
is never a pass.

**Activation is separate, and adds.** `POST
/api/setup/verification/activate` refuses a check that is not ready.
Registering a check or watching it pass is not a reason to require it.
The draft adds the chosen checks to the scope's existing requirements and
never removes one. It keeps an existing review requirement, and refuses
to replace it with a different one, which belongs in **Settings → Merge
rules**. The preview names the effect and the limit of the chosen review
policy: a Bench verdict is the review agent's, not a person's
independent approval, and GitHub refuses a formal approval from the
account that opened the pull request. Applying changes the policy and
not the check, so a pass with a result identity at the commit about to
move still counts and the merge needs no rerun for it; a pass recorded
before result identities existed reads `stale` (`policy-changed`), and
the merge waits for one new run against that commit. That is the gate
working, not a fault.

Who does what: the **author** (your team, or the agent on a verification
setup card) writes the program and a controlled failing case and says
what the program does not cover. The **operator** approves the command,
its preparation, its network use and its data, registers the check, and
separately decides whether a merge rule requires it. The **reviewer**, a
person and not the run that wrote the program, records the review, which
is neither a card's acceptance nor a formal GitHub approval. **Bench**
discovers without running, validates drafts, runs approved checks,
records evidence bound to source, policy and program, and enforces only
the rules you activated.

`GET /api/setup/verification` (also under `verificationReadiness` in `GET
/api/setup`) reads everything and writes nothing. The three POSTs sit
behind the same door as a policy write. Register and activate return a
`document` and its `revision` for `POST /api/settings/apply`, which is the
only writer of the policy.

### Application previews: configure, prepare, start, check, stop

A start script in `package.json` is not a working preview. The guide's
**Application preview** panel (`previews.py`) turns one into a preview you
can trust by keeping five acts apart, each its own control:

| act | what it does | runs project code | writes |
| --- | --- | --- | --- |
| **Configure** | validates a draft of `manager/local/preview.json` and saves it on an armed click | no | that one machine-local file |
| **Prepare** | runs the declared preparation steps (dependencies, a disposable fixture) in the card's worktree, each in its own process group, cancellable | **yes** | what the steps write, and a preparation record |
| **Start preview** | launches the configured command in the card's worktree on a loopback port bench checked was free | **yes** | what the app writes, a log, the drive record |
| **Check readiness** | asks the configured path over loopback, now | no | nothing |
| **Stop** | SIGTERM to the group bench started, SIGKILL after its grace period, then reports what is left | no | the drive record |

Discovery is not an act. It reads the application's files through the
setup cards' own inspection (`setuptasks.inspect`) and **proposes**: start
commands with port hints, a dependency step, environment names from an
`.env.example` (secrets marked as references, values never read), and data
prerequisites. Nothing is run, installed or started by reading the guide,
`GET /api/setup` or `GET /api/preview`.

**It rides the drive.** A started preview *is* a drive. `drive.start`
takes a launch spec bench built instead of the driver. That gives one
preview or drive at a time, one process group, one log, SIGTERM to park and
re-adoption after a restart. A board with no preview configuration behaves
exactly as before. The ⛭ chip, the driver contract and `DRIVE URL:` are
unchanged. The spec adds only what the guide needs:

- **Source binding.** The commit, branch and a digest of uncommitted work
  are recorded at start, with the configuration's revision. A later commit,
  an edit or a changed configuration makes the preview read `stale`, with
  the reason. The old process is never relabelled as current. A preparation
  is bound the same way: Start refuses one that is not current (`prepare
  first: …`). Start does not run the preparation steps itself.
- **Readiness means an answer.** `ready` means the configured path answered
  with the configured status, and with the configured text when one is
  set. A status code alone is called out (`readiness-shallow`), because any
  server on that port would pass it. A process that prints a line is not
  ready.
- **Ownership.** The child carries an owner token. A restarted board
  re-adopts a preview only when `/proc` shows that token, then asks
  readiness again (`reconnecting`) before saying `ready`. A group it cannot
  prove is its own is left alone and never signalled. Stop, a readiness
  timeout and an exit all signal only that one group.
- **Honest endings.** `unready` (the timeout passed; bench stopped its
  group), `failed` (it exited before it was ready, with its exit code and
  last line), `exited` (it exited by itself after being ready) and
  `stopped`. Each records `cleanup`: whether the group is gone, and whether
  the port is released or held by something bench did not start, which is
  then left alone.

**What a configuration may say**, checked where it is read and refused
with a code and a remedy: one program per command (no `&&`, pipe,
redirection, substitution or background `&`), named on PATH or by absolute
path; `{port}` and `{host}` as the only placeholders; `host` loopback and
`network` `"loopback"`; no `0.0.0.0`, bare `--host`, `--public`/`--share`,
tunnel programs (ngrok, cloudflared, ssh, socat, …) or safety-bypass flags
(`--no-sandbox`, `--disable-web-security`, …); no credential written as a
value (`literal-secret`), no publication or agent credential referenced
(`credential-reference`), no `*_ENV=production` (`production-default`), and
no migration, seed or reset step without `"data": "disposable"`
(`destructive-undeclared`). Remote browser or service access is not
offered. It is provisioned separately, outside bench. Four things are
**called out** rather than refused: an external service URL, a step that
uses the network, a declared-disposable data step and a shallow readiness
check. Saving needs each of them acknowledged by code, so the risk is read
before approval.

**Secrets** are references (`{"secret": "NAME"}`), resolved at Prepare and
Start from `manager/local/preview.secrets` or the board's environment.
Bench refuses to read that file while group or others can read it. A
missing reference refuses with its name and nothing run. Values reach only
the preview's environment. Publication and agent credentials, `AGENT_*`
and the board's own address are removed by name. Log lines are scrubbed
when served, and each log is rewritten scrubbed once its process is gone.
A scrubber is not a proof, and the panel says so.

**Port conflicts and dependencies.** `"port": "auto"` picks a free
loopback port. A fixed port held by anything is refused (`port-in-use`),
and nothing is stopped. A program not on PATH is refused
(`unavailable-dependency`) before anything runs. A preparation step that
fails reads `prepare-failed` with its tail, and the preview is not started.

**Disposable data.** `fixture` names the preparation step that loads it.
When the project has service or data configuration and no fixture, the
area says so, and the fixtures setup card (#89) is the way to get one.

**Not applicable.** For a library, a CLI or infrastructure, discovery finds
nothing to serve and suggests a smoke check. Marking the area not
applicable takes a reason and, optionally, an `alternative`: a registered
named check (`{"check": "cli-smoke"}`) or a card (`{"task": "42"}`).
Either is validated, and stored in `setup/decisions.json` with the
decision.

The area reads `configured` once the configuration is valid. It reads
`tested` only after a preview of the current configuration reached ready
and stopped with its group gone, and `failed` when the last one did not.
Browser automation is not assumed from an HTTP answer. A UI smoke test is
its own setup card and named check.

`GET /api/preview` (also under `applicationPreview` in `GET /api/setup`)
and `GET /api/preview/log` read. `POST /api/preview/{validate, configure,
prepare, cancel, start, check, stop}` sit behind the policy door. Prepare
and start also need `"approve": true`, and configure, prepare and start
carry the configuration revision the page read (`expected`), so a stale
page is refused. Records are machine-local under
`manager/local/state/preview/`.

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
  `## PR review at <commit>` section to the task file ending in
  `PR REVIEW: APPROVE | REQUEST CHANGES`. It runs in a checkout bench
  pinned to the exact revision that PR proposes, not in the application —
  see "A review runs in the revision it is of". Its verdict is also
  recorded as **bench's own**, bound to that revision, whether or not
  GitHub accepted a formal review of it: the account that opened a pull
  request cannot approve it, and the comment that attempt falls back to
  is never read as an approval — see "Gates: nothing merges on a report".
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
file. Then **◔ review PR** again, until it settles. It refuses a **merged
or closed** pull request by name, before a worktree or a model exists: a
push there would not change that PR and would not reach its base, and
saying "acted on the PR" about it would be a report of a change that moved
nothing (see "Historical, as against the integration head").

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

### A review runs in the revision it is of

A plan says which checkout a *work* run works in; a profile says what a
run may do there; verification says whether the project's checks passed
and against what. This is the question those three left open on the
read-only side: **which revision is being reviewed, and where does the
reviewer execute it?**

**◔ review PR** used to launch in `config.REPO` — the live application
checkout. Three things followed, and each is a reason it no longer does:
it is not the reviewed code (the checkout is on whatever branch somebody
left it on, with whatever they have edited in it); it is not pinned (a
push lands mid-review and a verdict about "the branch, whenever you
looked" cannot say what it covered); and it is shared, which is why a
project's own verification wrapper is right to refuse it and why `app` has
never been an execution root for a check.

So bench resolves the pull request's repository, base and head itself,
brings that commit into the application's object store, and adds a
**detached worktree of that one commit** under
`manager/local/state/reviews/`. The run works there. A detached checkout
has no branch to follow, so the revision cannot change under it, and
nothing the run does reaches a tracked file of anyone else's checkout.

- **Resolved before anything is created.** The identity reads, fetches
  (bounded by `BOARD_REVIEW_FETCH_TIMEOUT`) and answers; then the profile,
  against the path the checkout *will* be at; then the checkout; then the
  manifest. So a refusal leaves no checkout, no record and no run.
- **A head bench cannot name refuses the review.** There is no
  second-best revision, so there is no fallback to the application
  checkout — which is the behaviour this changed: a card with no branch
  and a `gh` that cannot answer used to launch a review of whatever was
  lying around.
- **`gh` is the authority, and the weaker one says so.** When `gh` cannot
  answer, the tip of the card's branch in this checkout is pinned instead
  and the record reads `authority: "local"`, in those words — the bargain
  `plans` strikes with `local-only`. The run is told, so a verdict cannot
  read as though GitHub's head had been checked.
- **Read-only stays read-only.** A pinned checkout is not a licence to
  write in it: `review` still denies `edit`, `commit` and `push`, has no
  writable root, and publishes nothing but its verdict.
- **A relevance check pins nothing.** **◔ still true?** shares the
  `review` intent and reviews no pull request, so it provisions nothing,
  fetches nothing, asks GitHub nothing and runs in the application
  checkout exactly as it always did.

**A check may run there too.** `review` is an execution root for a named
check (beside `worktree` and `source`), and it is the checkout the asking
run was pinned — read off that run's frozen manifest, so a check runs
against the revision the review is of rather than against whatever the
branch has since become. Bench refuses rather than repairs: a run with no
pinned checkout is refused the check by name, a directory that is a
checkout of something else is refused, and one sitting at another commit
is refused instead of being reset. The checkout outlives the verification,
so the runner does not remove it — its own `_cleanup` leaves that to the
review's ending.

**Ownership, and what it buys.** One record per run, written before the
model is invoked, naming the task, the run, the checkout, the base and
head identities, the pull request's state and the process that made it.
Machine-local, for the manifest's reason: it holds this machine's paths.
From it: a second run never reuses a first's checkout (a directory
another task or run owns is refused by name); cleanup removes only what
bench made, only inside the reviews directory with every link followed,
and never one with uncommitted work in it; a board that died owns up on
the next start, releasing what it was holding under exactly those guards
and reviewing nothing again; and a launch that fails after provisioning
gives the checkout back rather than leaving an orphan nothing can account
for.

**Freshness is the head, not the age.** `reviews.freshness()` compares the
pinned head with what the PR poll last saw — one authority for "what is
the head now", fed by the beat that already runs, so the question costs a
dictionary lookup rather than a call to GitHub per card. A head that has
moved makes the review `superseded` and its evidence `stale` with the
reason `head-changed`; a merged or closed pull request makes both
`historical` with `pr-ended`. Neither is ever shown as the new head's
approval, and neither is retracted: the review said what it said, about
the commit it said it about. The heading the task file keeps carries the
revision and the word, because the file outlives the checkout, the run and
the pull request.

### Historical, as against the integration head

A merged or closed pull request is not a thing to push to, and a phase
member's PR is merged into its phase branch early — so most member reviews
end up historical by design. "A historical review of what a member
proposed" and "validation of the current integration head" are two facts
about two revisions, and the board keeps them apart:

- **↻ act on PR refuses an obsolete target**, naming it, before the
  profile, the worktree or the model. Nothing is pushed and nothing is
  retargeted; the refusal names the integration head as the separate
  target it is — the card that owns that branch, which is a phase card's
  own PR.
- **The record says which it was.** `target` is `member` or
  `integration`, and `label` is `current`, `superseded` or `historical`.
- **Nothing on the page implies otherwise.** A `⌾` chip in the card's
  footer row, beside `⚿`, `⌖` and `◫`, opens the record: the base and
  head identities with the authority each was read from, the owned
  checkout and what became of it, and the checks that ran in it — in
  words, with `superseded` in `--alarm` and `historical` in the idle
  register, never in the colour of a pass. The sheet is read-only in both
  halves of the word: it provisions nothing, fetches nothing and starts no
  validation, because opening a historical review must not be a way to set
  something running against a branch it never judged. A review that ran no
  checks reads "no checks ran here" rather than nothing at all.

A phase member's PR is opened against **its phase's branch**, not `main`:
its branch was cut from there, so that is the only base whose diff is the
member's own work — and a PR into `main` carrying a whole phase is exactly
the merge this design refuses to make. Every other card, the phase card
itself included, opens into `main` as it always did.

The card wears that verdict in the design system's state colours:
approved → pine (`--calm`) border and an `approved` pill; changes asked →
terracotta (`--alarm`) and a `changes asked` pill; otherwise it wears no
pill, since the Review column already says it is waiting on you. Tool
chips (CI, copilot, PR, drive) are destinations, not statuses. Their full
set is in the card's **⋯** tools and its detail view (see "The card face
and its tools"). The ones that change what you do next stay in the
card's footer row, never in the author row: `CI ✕` (terracotta), `◌`
while in flight, a live drive. Hover actions stay in the status pill's
slot.
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
changes), **just move the card** (only the ticket moves — branch, PR and
worktree stay, and nothing is merged or pushed), or **merge & clean up** —
park the drive if it is this task's, merge the branch into the
application base, publish it and confirm the remote holds it, delete the
remote branch, remove the worktree and local branch, then move the card
(see "Done means delivered" below for what happens when publishing
fails). Every
step narrates in the ticker; a merge conflict aborts cleanly and the card
stays put. On a phase card the same action also finishes the cards the
phase merged — see "Finishing a phase finishes its cards". Cards without
work move silently, and hand-moves on disk are never intercepted — the
board only asks when you act through it.

Where this board declares an acceptance gate, **merge & clean up** is
refused until it is met: once before the drive is parked, so a refusal
costs one read and changes nothing, and once more immediately before the
merge, so a push that lands while the drive comes down is a refusal
rather than a stale reading. The sentence is the same one the card's `⚖`
chip shows — see "Gates: nothing merges on a report".

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

Where the merge is made is `BOARD_APP_MERGE`, read through
`config.app_merge_on_origin()` and nothing else. Unset, it keeps the old
answer: with the tickets inside the application's repository, `BOARD_SYNC`
on means the merge is made **on origin** — the board runs `gh pr merge` on
the card's PR, and local `main` fast-forwards to the result on the next
beat. Replicas converge only while main advances by fast-forward, so no
board makes a merge commit of its own. Two consequences the local path
hid: whoever clicks needs merge rights on the repo, not just push rights,
and a branch without a PR is refused with a pointer to **↑ open PR** —
there is nothing for origin to merge otherwise. With the tickets in a
repository of their own, ticket sync says nothing about the application,
so the unset default is the local merge; `BOARD_APP_MERGE=origin` or
`=local` decides it explicitly in either layout.

### Done means delivered

`done/` is a claim that the card's code is in the application base *on
the application remote*. So **merge & clean up** is four outcomes, each
waiting on the one before: the merge, its publication, the cleanup, and
the ticket's move — and the result says which of them happened.

**The sheet describes the completion that would run.** It asks the
server (`GET /api/task/completion`), which answers from the authorities
the completion itself reads — the merge mode from `BOARD_APP_MERGE` as
above, the target from the card's recorded plan, the remote from
`BOARD_GIT_REMOTE` — never from ticket sync on its own. It names the
target, so a board whose base is `trunk` says `trunk`, and says which of
three things will happen: a local merge pushed to the remote, a PR merged
on GitHub, or — for an application with no remote — a local-only
completion, with nothing published and nothing claimed to be. Where the
tickets live in a separate repository it adds that ticket sync publishes
the card's move and does not merge or push application code. A card
stacked on another branch, a target that has gone, or an origin merge
with no PR shows the refusal in place of the merge button.
**Just move the card** stays: it moves the ticket and invokes no merge,
push or cleanup — and a card in `done/` that way is not a record that
the code was published.

**Publication is read off the remote, not off an exit status.** After the
local merge the board pushes the base, then reads the application remote
itself (`git ls-remote`, fetching that one branch when needed) and asks
whether it contains the merged commit. After a GitHub merge it asks the
same question. A push that reported failure but landed — a reply lost to
the network — counts as published; a push or a `gh pr merge` that
reported success while the remote does not hold the commit does not, and
neither does a successful push of the *ticket* repository. On origin the
remote is also read before `gh pr merge` is asked, so a merge that landed
on an earlier attempt is not asked for twice.

**A merge that did not publish stays in `review/`.** The branch, the
worktree, the remote branch and a phase's members are left exactly as
they are, nothing is rolled back (local base has already advanced, and
the board says so rather than pretending otherwise), nothing is forced,
and the card wears `not published` in `--alarm` with what the remote
said — a protection rule, a rejected hook, an unreachable host — on
hover. The sheet, reopened, says what already happened and what to do: a
remote that has commits local base lacks is reported as diverged, to be
brought in by pulling and merging, never by forcing. Branch-protection
refusals are shown as given and are not bypassed.

**A retry reconciles, and a restart does not forget.** The moment a merge
lands, a record of what it merged, into which target and how is written
under `manager/local/state/completions/` — machine-local, beside the
recorded plans, and never in the ticket. Dragging the card to `done/`
again (**Retry publication & clean up**) reads that record: it does not
merge again, re-reads the remote, pushes if it still has to, and only on
a confirmed publication cleans up and moves the card. A branch that
gained commits since the merge keeps them, and the card stays in
`review/` as a partial completion. A record whose target or merge mode no
longer matches the board is refused rather than finished, and **Discard
this record** in the sheet — armed, and changing nothing in git — is the
explicit decision for a merge somebody settled by hand.

**Cleanup is reported as it went.** A dirty worktree is kept with its
branch, a branch git will not delete is reported as kept, a remote branch
that gained commits is left alone — and none of them is narrated as
removed. These do not hold the card back: the code is delivered.

The same contract holds for a phase card's own completion (its members
are swept only after its publication is confirmed) and in vendored and
external layouts alike — the application remote is always the one
`BOARD_GIT_REMOTE` resolves, the ticket's move is committed and synced in
the work state's own repository, and the two never stand in for each
other.

**Delivering source is not promoting the operator.** Completing a card
whose work changes bench itself puts that code in the candidate
application's base. It does not update the bench installation serving the
board — that is the separate, deliberate promotion described in "An
operator install, and the bench under test".

## Refining a card: comments and agent-proposed edits

A card is often not right the first time it is written, and the person
who notices is usually looking at it on the board. Refining it there
used to mean leaving the board to rewrite Markdown by hand. Now the
card's detail view has a **Discussion** under the brief, and three
actions that are deliberately three:

- **Add comment** saves what you typed to the card, with your name and
  the time. That is all it does. No agent is asked anything because a
  comment exists, and no old comment is ever read as a fresh instruction.
- **✎ Update card with agent** saves what you typed as a comment *and*
  asks for one refinement of it — or, on a comment already in the
  discussion, asks for a refinement of that one. It costs tokens, so it
  arms on the first click and fires on the second.
- **Apply** or **Discard** decides what came back. Until one of them is
  clicked, the brief is exactly what it was.

**Refining a card is not implementing it.** A refinement reads the card,
its discussion and — where that grounds the requirement — the
application's code, and proposes a revised **brief**: the description,
the scope, the acceptance criteria and the notes. It creates no branch
and no worktree, edits no application file, runs no command, moves no
card, opens no pull request and never starts a work run. Changing the
brief does not retarget a plan already recorded for the card either:
**⌖ replan** and **▸ start work** stay the separate, explicit actions
they always were.

### Brief, history and discussion

A card is three parts, and refining only works because they can be told
apart by reading the file:

- the **head** — the title and the `**Field:**` lines under it: the
  card's identity, its lifecycle and its publication metadata (Status,
  Assignee, Base branch, PR base, PR and the rest). A refinement never
  touches it.
- the **brief** — everything after the head up to the first piece of
  history. The active requirement, and the only part a refinement may
  propose to change.
- the **history** — from the first dated report the board appended
  (`## Work report — <date> (<name>)`, with the `---` above it), or a
  `## Phase log`, `## Discussion` or `## Acceptance record` section,
  to the end. Reports and reviews stay here, as history rather than as
  requirements, and every write below carries them over byte for byte.

The discussion is one section, `## Discussion`, created at the end of the
card the first time something is said. Each entry is an `###` heading, a
one-line machine-readable marker, and the text itself as a blockquote.
Quoting is the safety: a comment that says `**Status:** Done`, `PR
REVIEW: APPROVE` or `## Cards` is a line beginning with `>`, so it cannot
pose as a header, a verdict marker or a phase list to anything that reads
the card. The detail view draws the brief without the discussion and the
discussion as plain text; nothing anyone typed is rendered as markup.

### What you see while it works

The comment that was the instruction stays where it is, and under it the
refinement's state in words — `refining…`, then `proposal — not
applied`, `failed`, `cancelled`, `applied`, `discarded`, or `superseded
by a newer attempt`. A proposal shows the agent's explanation, which
sections it touches, and a focused before/after diff of the brief, with
**Apply** (armed) and **Discard** beside it. A refinement that is still
running can be cancelled; one that failed shows why and offers **↻ Retry**.

Apply replaces the brief, records an `Applied` decision in the
discussion naming the comment and the proposal it incorporated, and
commits the card once — in the **work state's** repository, through the
same write-and-commit path every other board edit takes, never as an
application commit. Discard records a `Discarded` decision and leaves the
brief alone. Both outcomes stay readable in the card's history.

### Why a refinement cannot do more

The refinement is its own launch intent, `refine`, and it is the
narrowest there is: no worktree, no command grant, no check to ask for,
no edit tool and no shell, the last two withheld in the vendor's own
tool roster rather than asked for in a prompt. It starts in the
application checkout so it can read the code, and its only output is a
proposal printed at the end of its run. Bench then validates it **on the
server**, against the brief the run was given, and refuses — with nothing
written — a proposal that:

- is empty, or larger than 64 KiB;
- contains the `# ` title or any `**Field:**` header line;
- contains a history heading, or a line the board parses as a marker
  (`PR REVIEW`, `NOT READY`, `RELEVANCE REVIEW`, `REFINEMENT …`);
- contains a `<!-- bench:` marker outside code;
- opens a ``` fenced block and never closes it;
- changes a phase card's `## Cards` list;
- invents an acceptance-criterion id, or uses one twice.

**Documenting the storage syntax is not writing a record.** A card may
need to show what a discussion entry or an acceptance record looks like —
card 81 does. Inside inline code, or inside a ``` fenced block that
closes, a `<!-- bench:` example is literal text: every Markdown renderer
draws it as text, the board draws it as text, and the board reads
records from a card's history only, never from its brief — the recheck
an applied refinement makes included. So such an example is allowed in a
proposal and survives Apply and a reload as documentation. The same
characters anywhere else are an HTML comment shaped like a record, and are
refused with the line they sit on, whether or not the old brief already had
one. "Code" means exactly what the history split means by it: a line whose
first non-blank characters are ``` opens or closes a fence, and an inline
span closes on its own line with the same number of backticks it opened
with. `~~~` fences, four-space indented blocks and a span that does not
close are not code here, so an example in them is refused rather than
guessed at — and an unclosed fence is refused outright, because the
history split would read the card's history as part of the brief.

Code is no shelter for the rest of the list. The `# ` title, `**Field:**`
header lines, history headings and the verdict markers are read by line
wherever they appear in the file, code blocks included, so a proposal
containing one is refused even inside a fence, and the refusal says so.

A refused proposal reads `failed`, names the reason, and changes nothing.
So does a run that ended without a proposal, one that declined (`the
agent declined to refine the brief: …`), one that crashed, and one that
was cancelled.

It also only runs where it can be held to that. An adapter's
`capabilities.json` must list `refine` among its `intents` and declare
`read-only-tools` and `shell-withheld` as `enforced`; anything less is
refused by name (`intent-unsupported`) rather than handed an intent it
might run with implementation's stance. A profile document that gives
`refine` commands, checks or preparation steps is refused
(`intent-restricted`); the document's shared lists never apply to it.
Both shipped adapters qualify. The **⚕ Configuration check**, the Focus view's profile
panel and **Settings → Agent access** show the `refine` intent beside the
other three, with what it may do and what is refused — and reading any of
them launches nothing. `BOARD_AGENT_MODEL_REFINE` picks its model.

### Concurrency: nothing is lost, nothing is applied twice

- **A proposal is bound to what it read** — the card's stage and the
  revision of its brief. At Apply both are checked again under the write
  lock, and the file is compared once more immediately before it is
  replaced. A brief edited by hand, changed by a sync, or changed by
  another applied proposal makes the proposal **stale**: Apply refuses,
  the card says why, and **↻ Regenerate** asks again against the brief as
  it now is. A card moved to another stage is stale the same way.
- **Everything outside the brief is kept.** A comment another client
  added, a report an agent appended, a header the board wrote — all of it
  arrives between the proposal and the Apply without making it stale,
  because Apply only ever replaces the brief and carries the rest over.
- **One click, one job.** Each request carries an identity, so a double
  click, a resent request or a refresh answers with the job it already
  made. A comment with a refinement in flight or a proposal waiting gets
  that one back. A comment whose last refinement ended is asked again only
  through **↻ Retry**, which is how a deliberate retry is told apart from
  an accidental replay.
- **Apply and Discard are safe to repeat and exclude each other.** The
  second Apply answers "already applied" and writes nothing; a Discard
  after an Apply, or an Apply after a Discard, is refused. A decision a
  replica already made — read off the card after a sync — is honoured
  rather than made again.
- **Restarts.** Proposals and job states are kept under
  `manager/local/state/refine/`, machine-local like plans and manifests,
  so a proposal waiting for a decision survives a restart. A refinement
  that was running when the board stopped is marked failed, naming the
  restart, and is never re-run by itself.
- **Active implementation.** While a work or act-on-PR run is alive on
  the card — or another agent, or a merge and clean up — refining and
  applying are refused with the reason and the way through: hold the run
  with **‖ hold** first. Nothing is stopped automatically, and a running
  agent does not see comments added meanwhile. **Add comment** is always
  available.
- **Commit and sync, truthfully.** With `BOARD_COMMIT_MOVES` on, every
  comment and decision is its own `board: <n> …` commit touching only the
  card, and `BOARD_SYNC` publishes it like any move. With it off, the
  card file is written and not committed, and the discussion panel says
  so. Vendored and external layouts behave the same; externally, the
  commits land in the work state's repository and the application's
  history does not move.

What is not shared, on purpose: a proposal nobody has decided yet exists
on the board that asked for it. It is a suggestion, not a requirement,
and it is never a second source of what the card says — the card is.

### The persistence contract (v1)

Written for the card that will read it next (#81, structured acceptance)
and for anyone editing a card by hand. Every marker carries `"v": 1`.

```markdown
## Discussion

<!-- bench:discussion v1 — written by the board. Entries are history: the brief above is the requirement. -->

### Comment c-0a1b2c3d4e — ron · 2026-11-08 15:40 UTC
<!-- bench:entry {"at": "2026-11-08T15:40:00Z", "author": "ron", "id": "c-0a1b2c3d4e", "kind": "comment", "v": 1} -->

> The comment, quoted line by line.

### Applied p-5f6a7b8c9d — ron · 2026-11-08 16:02 UTC
<!-- bench:entry {"at": "…", "author": "ron", "briefAfter": "…", "briefBefore": "…", "comment": "c-0a1b2c3d4e", "id": "d-1a2b3c4d5e", "kind": "decision", "outcome": "applied", "proposal": "p-5f6a7b8c9d", "sections": ["Acceptance"], "affected": ["saved"], "v": 1} -->

> What was applied, and the agent's explanation.
```

- An entry's identity is its `id` (`c-…` a comment, `d-…` a decision);
  `kind` is `comment` or `decision`, and a decision's `outcome` is
  `applied` or `discarded`, naming the `proposal` and the `comment` it
  answered. `briefBefore`/`briefAfter` are short brief revisions.
- **Acceptance criteria** keep a stable identity when they carry one on
  their own line — `- [ ] Saving a comment runs nothing <!-- ac:no-run -->`.
  Identity is the id, never the position, so reordering an unchanged
  criterion changes nothing about it. A refinement may keep, reword or
  remove an identified criterion and may add unidentified ones; it may
  not invent an id.
- **Acceptance records** are history, one per line, written by the board:
  `<!-- bench:acceptance {"criterion": "no-run", "state": "accepted", "v": 1, …} -->`,
  under `## Acceptance record`. When an applied refinement rewords or
  removes an identified criterion, every record about it gains
  `"recheck": true`, `"recheckReason"` and `"recheckAt"` — in the same
  write that replaces the brief, so the two cannot disagree. Discard
  changes neither. A card with no ids and no records keeps its Markdown
  exactly, and no acceptance decision is invented for it.

Fixtures that hold this contract to the code are in
`tests/fixtures/refinement/`.

## Acceptance and review findings

A card's checklist says what must be true. Until now nothing recorded
whether it was: a Done card with every box unticked and a Done card with
every box ticked by hand said equally little. Acceptance is the record —
per criterion, attributed, bound to the requirement's wording and the
code it was judged against — and review findings are the same idea for
what a review turned up.

**It is a record, not a default ceremony.** The PR review already judges
the work against the card's acceptance criteria, and no merge rule on a
board requires per-criterion sign-off unless the operator configures
one. So nothing on the board asks a person to confirm criteria as a
matter of course: the Overview never says "confirm acceptance", the
accepted count is one of the card's tools rather than a face chip, and
on a delivered card the fact that some criteria were never accepted on
record is stated in the Acceptance section, as a fact, rather than as a
next step. Every control and every record below is still there and
works exactly as described; a criterion a person recorded as needing
work, and an open finding, are current problems and are on the face.

### Tracking is explicit, and viewing changes nothing

A card nobody has tracked reads **Acceptance results not recorded**, in
those words, whatever lane it is in. Its criteria are listed, and a
hand-written `[x]` is shown as what it is: a legacy claim, not an
acceptance. Opening the card writes nothing.

**Track acceptance** (armed: it edits the brief) is the one operation that
gives criteria identities. Each `- [ ]` line without one gains
`<!-- ac:<id> -->` at its end — a fresh id, never one a removed criterion
held — and an acceptance record: `pending`, or `reported` with source
`legacy-tick` where the line was ticked by hand. The tick and the wording
stay exactly as they were. It is bound to the brief you were shown, so a
brief that changed meanwhile is refused with nothing written, and because
it edits the brief, a refinement proposal made before it reads stale.
Criteria added later begin pending and are tracked the same way.

### What a criterion's state means

| state | what it licenses you to believe |
| --- | --- |
| `pending` | nothing is on record about it |
| `reported — awaiting confirmation` | a run said it implemented it, or it was ticked by hand before tracking. A claim |
| `accepted` | a named person confirmed it with a note, or mapped one of bench's own records that reads `passed` *now* to it |
| `needs work` | a person recorded that it is not met, and why |
| `needs recheck` | it was accepted, and what that rested on has moved: the wording changed, an applied refinement marked it, or the evidence no longer reads `passed` |

Only `accepted` wears the tick and only `accepted` is counted. An agent's
report, a clean exit, an unrelated passing suite, a merge and a move to
`done/` change none of them. A recheck keeps the earlier decision in view
and says why it no longer stands; reordering criteria changes nothing,
and a removed criterion leaves the count and stays in history.

### Recording a decision

Each criterion expands to its full wording, its id and revision, what
supports it, and its whole record. A decision names its actor — this
board's git identity in the work state, else the operating-system user,
said which — and is refused rather than written when the page is stale:
it carries the criterion's revision and the newest decision the person
saw, so a slower second page or a replayed request cannot overwrite a
newer decision, and a request key makes a double click one record.

- **Accept** needs a note saying what was checked (a manual or visual
  confirmation, optionally with a reference such as a screenshot), *or*
  one of bench's evidence records for this card that reads `passed` now.
  Mapping evidence is the person's act; nothing maps a check to a
  criterion by its name or a test count.
- **Needs work**, **Needs recheck** and **Back to pending** need a note.

A run reports a criterion through its progress channel —
`bench-progress --criterion <id>` — and that is all it can do: the card
shows *reported* beside the run that said so, and a person decides.

### Review findings, their fixes and their rechecks

Each `## PR review …` section an agent filed is a **round**, identified
by its heading, with its reviewer, the revision it was pinned to and its
verdict. A round's verdict is about that revision: a request for changes
stays what it was, and the Overview and Reviews sections say what has
happened since (PR updates, fixes reported, findings resolved). Neither
section ever reads a historic request as approval, and resolving findings
here is not GitHub's formal approval — Signals keeps those apart.

A **finding** is recorded by a person (**Record a finding**), optionally
against a round and a criterion — not every concern is a criterion. Its
life is a chain of events: `opened`, `fix-reported`, `resolved`,
`reopened`. A reported fix keeps it open until somebody rechecks it;
resolving it needs a note on what authority (and optionally a passing
evidence record) and records the branch revision; a later agent update
never resolves anything. Open findings and fixes to recheck drive the
Overview's next step.

### Storage (extends the v1 contract above)

All of it lives in the card, in the history half `card_parts` already
keeps apart from the brief, so a refinement never touches it and sync
carries it like any other board edit (`board: <n> acceptance …`, one
commit per write under `BOARD_COMMIT_MOVES`).

```markdown
## Acceptance record

<!-- bench:acceptance-record v1 — written by the board. One decision per line; the newest about a criterion stands. -->

<!-- bench:acceptance {"v": 1, "id": "r-…", "criterion": "a1b2c3", "rev": "<12 hex>", "state": "accepted", "source": "manual", "actor": "ron", "actorSource": "git", "at": "2026-11-09T10:00:00Z", "text": "…", "note": "…", "ref": "…", "commit": "…", "supersedes": "r-…"} -->

## Review findings

<!-- bench:review-findings v1 — written by the board. One event per line; a finding's newest event is its state. -->

<!-- bench:finding {"v": 1, "id": "e-…", "finding": "f-…", "event": "opened", "text": "…", "round": "rv-…", "criterion": "a1b2c3", "actor": "ron", "at": "…"} -->
```

- `rev` is a hash of the criterion's wording at decision time; a record
  whose `rev` differs from the criterion now reads `needs recheck`. #65's
  Apply still marks affected records `"recheck": true` in the same write
  as the brief, and Discard changes neither.
- `source` is `tracked`, `legacy-tick`, `manual` or `evidence`; an
  evidence-backed record also names `evidence`, `check`, `scope` and the
  commit the evidence ran against, and is re-read against bench's own
  record every time it is shown.
- A finding's `opened` event carries its text; later events carry a
  `note`, the `commit` they were made at, optional `evidence`, and
  `supersedes` naming the event they answered.
- Records are appended, never edited. The newest record (by `at`, then
  file order) is the state; every earlier one is history.
- A run's criterion claims are machine-local, in its progress record
  (`criteriaReported`), like the rest of what it said.

The HTTP surface is `GET /api/card/acceptance` (reads, writes nothing),
and `POST /api/card/acceptance/track`, `/api/card/acceptance/decide` and
`/api/card/finding`, each answering a stale request with `409` and
`"stale": true`.

## Phases

Some work is a run of related cards rather than one card: three that have
to land in order, each building on the one before it. A phase is how
bench holds that — a card that lists its cards, an integration branch of
its own, a beat that works the list one member at a time, and a single PR
into `main` at the end. Everything else on the board reaches it by
inheritance, because a phase card is a card.

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

**And then the member gate, where one is declared.** "No CI result"
advancing is exactly the case a configured gate exists to stop, so on a
board that declares one the member also has to carry bench's own passing
evidence for the commit about to be merged — read immediately before the
merge as well as on the pass that chose it. An unmet gate halts the
phase like any other condition, with the gate's own sentence as the
reason; a required check still running holds it. A board that declares
no gate resolves `ungated` and advances exactly as it always did. The
whole of it is "Gates: nothing merges on a report".

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

**And the card itself says which state it is in.** The header chip only
appears while there is something to say, so it cannot tell "the phase has
not been started" from "there is no phase here at all" — which left an
`in-progress/` phase card nobody had run looking exactly like one mid-run.
So the card carries the distinction quietly, in the pill and the line
under it: `not started` (`▸ run phase` cuts its branch and starts the
first card), `held`, the accent and the member in flight while it runs,
and `halted` in `--alarm`. Only a run wears the working vocabulary — the
breathing mark, the accent border, the caret — because only a run is work
happening.

**And the card does not move while its work runs.** A phase card stands
for cards the Board no longer draws, so dragging it to another stage — or
onto the archive tray, which is a move like any other — while a member has
an agent in it would leave the card in one place and a live run, a
worktree and a branch in another. That move is refused, and the refusal
names the way through rather than the wall: which member is working, and
that **‖ hold** stops the phase and the agent it has in flight while
leaving the phase branch, everything merged into it and every worktree
exactly as they are. The card stays where it was and the reason is in the
toast. The answer is the server's — `/api/move`, `/api/archive` and
merge-and-clean-up all ask it, so a stale page cannot get past a rule the
drag alone would enforce. What is refused is narrow on purpose: a phase
*between* members has nothing in flight and moves freely, a halted one has
nothing running by construction and moves freely — which is exactly when
walking it back is the thing to do — and "running" is read from the runs
themselves rather than from what the log says was once started, so a
member's run that died cannot lock its phase card. An ordinary card never
reaches the question.

Every advance is narrated in the ticker as it happens: the member that
came up green, the merge into the phase branch, the member starting next.
And the phase card's sheet lists its members in run order with each one's
stage — and, while a phase is in flight, the runner's own reading of each
(merged in, working, checking, stopped here) — so the card answers "where
is this up to" without a hunt across five columns.

### Finishing a phase finishes its cards

A member stops at `review/` on purpose: `done/` has always meant *merged
into `main`*, and merged into a phase branch is not that. **Merge & clean
up** on the phase card is the moment it becomes that. The phase's PR goes
into `main`, so every card the phase carried is in `main` too — and since
a phase reaching `done/` releases its members back onto the Board, the
alternative is handing back three or five cards you have already judged,
in the column whose note is "your move".

So the merge sweeps. After it has actually succeeded — never before, and
never if it conflicts — every member the phase merged moves to `done/`,
and each one's workspace is cleared exactly as completing an ordinary card
clears its own: the worktree removed, the local branch deleted, the branch
on the remote deleted. That half is not cosmetic. A stale worktree is a
trap laid for whoever reopens the card, since a work launch refuses a card
whose worktree already exists, and the accumulation is per member per
phase.

Four rules keep it honest:

- **Only what the phase merged.** A member is swept when its card settled
  into `review/` or `done/` *and* its branch is contained in the phase
  branch — the same pair the runner reads a member as `merged` by, and a
  member with no branch at all was finished before the phase reached it.
  One that halted, was held or was walked back is neither moved nor
  cleared: there is work in its worktree and its branch, and it is the
  reason a person will look at this phase afterwards.
- **Nothing uncommitted is thrown away.** A member's worktree comes out
  without `--force`, so one with uncommitted changes in it is reported in
  the ticker and kept, with its branch. Removing a worktree with work in
  it is the one unrecoverable step in the ending.
- **One ending, told once.** The cards move together in a single commit
  naming all of them (`board: 47, 52 → done with phase 53`), and the
  ticker gets one line — the phase, how many cards went with it, and how
  many it never merged and left where they are — instead of five moves
  scrolling past. In team mode that commit publishes like any other.
- **Only merging sweeps.** "Just move the card" leaves the work alone,
  including the members'. Archiving the phase card, or dropping a number
  from its `## Cards`, releases the members to the Board in whatever stage
  they are genuinely in (that is card 56's rule, and it is unchanged) —
  neither one says anything is done, because neither one puts anything in
  `main`. The members' own PRs are closed by their own merges into the
  phase branch and are not touched here.

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
back in the columns they are genuinely in. Which, when the phase got
there by being merged, is `done/`: the merge moved them (see "Finishing a
phase finishes its cards"). The other two endings move nothing, so the
cards they release are wherever they actually were. A membership that did
not resolve hides nothing either: a card wearing `phase drift` stays on
the board, because an authoring mistake must not make work vanish.

That leaves the phase card standing for all of it, so it carries the
summary it owes: a `⟶ 1 of 2 merged` chip in the footer row that opens
its own sheet (the member list, in run order, with each one's stage), the
member in flight on its activity line, and a run that halted worn in
`--alarm` like any other stopped work. Only the Board hides. Sessions and
Focus are about runs, not stages, and a phase member's agent is an agent
like any other — it is counted by the header's live chip and by the tab
title wherever it is working.

Where they are drawn instead is the next section. Filtering the board to a
phase's cards, and a Focus view for a phase, remain separate cards.

### The Phases view: a swimlane each

Where the members went. A fourth view beside Board, Sessions and Focus,
holding **one lane per phase card** — every phase there is, not only the
running ones, because a phase whose cards are all merged and which is
waiting on its own PR still owns them, and a lane that vanished exactly
when it needed merging would be a view you could not trust. Lanes are
ordered so a halt is never below the fold: halted, then running, then the
phases nobody has started, then the ones that are over. With no phase
anywhere the view says so in a sentence rather than drawing an empty grid.

A lane is a head, five stage columns and the phase's log:

- **The head** — the phase, its progress (`2 of 3 merged`), the member in
  flight on the same activity line a card would use, and its own controls:
  **‖ hold**, the phase branch, and the phase card, which opens in the
  drawer. Those are all of them. Nothing here ends a phase — merging is a
  board move on the phase card, and there is exactly one place where work
  leaves the board.
- **Five columns, and the last one is the phase's own.** Four are the
  board's stages; the fifth is `Merged in`, not `done/`, because a member
  merged into the phase branch is finished as far as the phase is
  concerned and is not in `main` yet. A member is drawn in the stage it is
  actually in until the phase branch holds it, and then in that last
  column whatever its card says. Which members the branch holds is the
  runner's last pass while a phase is running, and the phase log once
  there are no more passes — the same fact, written down by the same
  runner, which is what keeps a phase in `review/` from drawing a lane
  where nothing ever landed.
- **The cards are the cards.** `cardFor()` builds them here exactly as it
  builds them for the Board: the live agent line, the CI and PR chips, the
  `⟶ <phase> 2/3` chip that says where each sits in the run, and the same
  hover actions. Nothing is shrunk to a token — that fidelity is the whole
  advantage a room of its own buys.
- **The phase log, under the lane** — the runner's decisions in the order
  it made them, read off the `## Phase log` section of the phase card
  itself. It is the only thing that can tell "the phase has not reached
  this card" from "it started it and it ended badly", and until this view
  it had nowhere to be shown.

**A halted lane says so at the top of itself**: the reason, the card it
stopped on, and **▸ run again** beneath them. That action lives here and
not on the Board, because clearing a halt should mean having read what
caused it — the Board's halted phase card offers **⟶ phases** in its place,
and **‖ hold**, which needs nothing read.

The crossing matters more than the lane: a person on the Board has to
learn that a phase halted without being on this view, and a person here
has to be able to get back to the card that owns it. So the switcher
carries the running count and, in `--alarm`, a mark when something has
halted — on every view, holding until the phase is run again or held,
alongside the toast and the ticker line that already fire. And the lane
head's title and its `card ↗` chip open the phase card.

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

Start new tasks from `tasks/task-template.md` under the work root — copy it
into `backlog/` and fill it in. The installer seeds that file from the
tool's `manager/core/seed/` when it is missing, and it is yours from then
on: edit it freely, updates never replace it. Its sections earn their keep: Context and What-to-build are what
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
| **Base branch** | no | the application branch this card's work starts from | you |
| **PR base** | no | the branch this card's PR targets (defaults to **Base branch**) | you |
| **PR** | no | the pull request url | the board when it opens one |
| **Setup gap** | no | the setup gap a card answers (`instructions`, `fixtures`, `preview`, `ui-smoke`, `verification`) | the setup guide when it proposes the card |

**Status** is the field the board holds you to: a header that disagrees
with the directory the file sits in is flagged `status drift`.
**Assignee** and **PR** it writes and reads itself. **Base branch** and
**PR base** it holds you to in the same way as Status — a value that is
not a branch name, or one that contradicts the card's phase, is flagged
`plan drift` and refuses the launch. The rest are for whoever picks the
next card.

An optional **Type** line can record what kind of work the task is, when that
isn't obvious from the title:

```markdown
**Type:** Discovery | Bug | Feature | Refactor | Chore | Phase
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

The board parses the task numbers out of the line (prose preconditions are
left for the reader) and nothing enforces them yet: they inform whoever picks
the next card, and guard what a phase may start. It is explicitly *not* an
instruction to stack a branch or merge a prerequisite — a card that needs to
be built on another branch says so with **Base branch** below.

The optional **Base branch** and **PR base** lines are how a card that
belongs on a branch other than the application default says so, in
machinery rather than in prose:

```markdown
**Base branch:** task/56-external-workspaces
**PR base:** task/56-external-workspaces
```

**Base branch** selects the application branch the work starts from; the
PR target defaults to it, so **PR base** is only needed when the two
differ. Both are resolved and validated before anything is created, and a
named base that cannot be reached refuses the launch rather than quietly
reverting to the default — see "Execution plans" for the whole of it.
Omit both and the card behaves exactly as every card did before they
existed. A phase member must omit both: it inherits its phase's branch and
targets it.

The rest of the file is freeform — description, research findings, approach,
open questions, whatever is relevant to the current stage.
