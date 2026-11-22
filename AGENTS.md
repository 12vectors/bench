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

Module map for `manager/core/`, lowest layer first: a module imports
only modules to its left, at least at the top level. The authoritative
layer table, the convention (`A → B` means A imports B, so B is lower),
the proposed target layers and the reviewed, shrink-only list of
exceptions live in
[`manager/core/ARCHITECTURE.md`](manager/core/ARCHITECTURE.md), held by
`tests/test_architecture.py`. Since task 122 (WP13) that list is empty:
no core module imports another from inside a function, none reaches a
private name of another, and `board.py` imports every core module at
startup:

```
gitproc / records / redact < config < githost / assets / agreements / operations / decisiontrace < state / reports / templates / gitrepo / verifierreport / workbase < taskfiles < plans / profiles / execowner / movements / decisions / brain < gatepolicy / reviews / inputs / basechoice < evidence / resultidentity / artifacts < staleness < verify < progress / checkload < acceptance / callers < gates / brainreviews < required / orchestrator < doctor < commands / drive / events / sync < changes / brainwrite / reportfiling < completion < stacks < delivery / stackactions / results / cycletime < agentsettings < settings < setupbase / appinspect < agents / previews / readiness < setuptasks < setupguide < phases < phasescope < refine / reasoning / coordinate / braindraft < recovery < scheduler < ask < routes / watch < httpd < board
```

- `gitproc.py` — running git so a read stays a read: the read verbs, the
  `--no-textconv --no-ext-diff` switches, the hardened environment and
  the one subprocess call. Stdlib only; imports no core module
- `records.py` — how a record reaches the disk whole: a unique temporary
  file in the same directory and `os.replace`, `durable=True` adding the
  file and directory flush, tolerant reads, and the interprocess lock a
  record family with more than one writing process holds. Stdlib only;
  takes paths as arguments and knows no record family
- `redact.py` — what a credential looks like: one pattern table, the
  union of the ones `settings`, `agentsettings`, `previews` and
  `bootstrap.py` used to keep apart, masking only the secret part of a
  match. The marker is the caller's, so each page keeps its own words.
  Stdlib only; `verify.py` adopts it with the verify train (WP12)
- `config.py` — paths, stages, settings, prompt/adapter/driver resolution;
  `git_argv`, `git_env` and `git_run` as patchable wrappers over `gitproc`
- `githost.py` — the hosting provider in force (`BOARD_GIT_PROVIDER`:
  `github`, the default, or `none`), what it can do here and refusing by
  name; it loads `providers/<name>/provider.py` by path and dispatches
  every change-request operation, returning normalized values. The `gh`
  argument lists, GitHub url and payload shapes live in
  `providers/github/` and nowhere else; a remote the provider does not
  recognise is reported and never acted on. Contract:
  `manager/core/providers/README.md`. Imports `config` only
- `assets.py` — the page's static assets (task 123): `web/manifest.json`
  lists every file under `web/` with its sha256, size and the one type
  its suffix is served as, and the build id is a digest of that manifest.
  At start the board reads `board.html` and every listed asset into
  memory, verifies them and refuses to serve when one is missing, linked,
  oversized or changed; then it serves that snapshot under
  `/web/<build>/`, immutable, whatever an update does to the disk.
  `BOARD_ASSETS=disk` reads from disk, uncached, for editing. Imports
  `config` and `records`
- `state.py` — shared registries, event log persistence, SSE fan-out,
  and the board revision every `board` frame and state reading carries
  (task 128)
- `reports.py` — what the record keeps of an agent's closing report: one
  cap, one clip, for the task file and the PR body alike
- `verifierreport.py` — the verifier report contract, version 1 (task
  135): the bounded JSON envelope a check that opts in with `"report":
  "v1"` writes to `$BENCH_VERIFY_REPORT`, its validation, and the one
  rule that combines it with the exit status bench observed. Imports
  `redact` only; inside the runner revision (see "Verifier reports:
  actionable evidence from the check itself")
- `templates.py` — the prompt templates: the four launch templates and
  the orchestration prompts (the shared reasoning template and one
  guidance template per reasoning need, task 153), what each may and
  must contain, which text a launch actually used (name, source, hash),
  and the contract check asked before a save and before every launch
- `gitrepo.py` — local git on the two repositories, each named rather
  than reached for by path: application git (`app()`) and work-state git
  (`state()`, card 62's `app_shares_state_repository()` deciding whether
  they are one). A never-raising `git`, the worktree primitives, one
  `ensure_worktree` for every "recreate it from its branch", and
  `fresh_branch_point` — the default branch point, below `plans` and
  `agents` so neither reaches up for it. Imports `config` only
- `workbase.py` — a card's work-base decision read back off the card
  (task 150): the newest `base` record in its `## Orchestration record`,
  field by field, and whether a *new* start may act on it. Pure; imports
  no core module, so `taskfiles` attaches it to every card it reads
- `taskfiles.py` — reading and moving task files; the only code touching tasks/
- `plans.py` — one card's execution plan: which application branch its work
  starts from, which branch its PR targets, what selected each, and the
  preflight that validates all of it before a branch, a worktree or an
  agent exists
- `profiles.py` — the other half of the same decision: what a run may do
  in that checkout and which files it is guaranteed to have read. One
  profile per launch intent, the approved context it pins read-only, and
  the manifest recording what each run actually rode. Its request-scoped
  read context (`read_scope`, task 127) lets one board read ask the
  document, each resolution and every approved input once rather than
  once per evidence record; only `routes.state_payload` opens one, and it
  ends with the request
- `gatepolicy.py` — the merge rules as written: reading and validating
  the policy document's `"gates"` block (each scope's checks, review and
  sign-off policy, the override path), its public form, and the lines a
  committing run is told the rules require. It reads the document and
  the check definitions through `profiles` and evaluates nothing —
  `gates` does (task 120)
- `reviews.py` — the checkout a PR review runs in: the pull request's
  repository, base and head resolved before anything is created, a
  detached worktree of that exact head owned by the run, and whether the
  review still speaks for the head the pull request proposes
- `inputs.py` — a card's required information (task 132): the `## Inputs
  and required tools` contract in its brief, the references the brief
  names, whether the run that would start can use each input and tool,
  the one bounded check that retrieves a remote reference, and the inputs
  a launch adds to its manifest before the pin. One resolver for the
  Inputs tab, the move into in-progress and every implementation launch;
  reading it fetches nothing (see "Required information: inputs and
  tools")
- `basechoice.py` — choosing a card's work base (task 150): the
  application base, or a declared dependency's committed work in Review.
  `assess` reads local refs and parsed cards only; `decide` writes the
  decision onto the card once per set of inputs, immediately before a
  launch the scheduler is authorized to make; `recheck` holds the launch
  to it; `observe` writes a `stale` record when a stacked decision's
  parent moves, lands, goes back or vanishes (see "Choosing a work base:
  main, or committed work in Review")
- `evidence.py` — the verification record: where it lives, how it is
  stored (through `records`, durably once its outcome is final) and read
  back, the reuse records, the outcome vocabulary every reader branches
  on, and the evidence epoch a memo keys on (`epoch()`); a log is
  scrubbed by `redact`'s table (task 121)
- `artifacts.py` — retained demonstration outputs (task 133): what a
  check declaring `"outputs": true` wrote to `$BENCH_VERIFY_OUTPUTS`,
  kept per card under `state/artifacts/` with the run that made it,
  classified by its own bytes, bounded, withheld or expired with its
  record kept, and served only through the board (see "Show the result:
  demonstrations, not proof")
- `resultidentity.py` — what a verdict is about: every dimension of a
  result identity (source, check, coverage, programs, preparation,
  inputs, root class, environment) and the one owner of the runner
  revision, a digest over the declared `RUNNER_FILES`, with
  `BOUND_ELSEWHERE` saying why every other dependency is outside it
  (task 121)
- `staleness.py` — whether a stored pass still holds (`freshness`,
  `outcome_now`, `identity_drift`) and whether a record of another check
  may answer for this one (declared equivalence) (task 121)
- `verify.py` — the named checks bench runs itself, on a run's request:
  the argument list built here rather than composed by a model, the
  environment the check is given, the reservation registry and reuse
  selection held under one lock, cancellation and recovery — and the
  evidence it leaves, the runner's own exit status bound to the source it
  ran against
- `progress.py` — what a run *says* about its own work: the one
  run-scoped channel it reports through, each report validated whole at
  ingestion, ordered, de-duplicated and coalesced, closed by the run's
  observed ending, and kept apart from anything bench verified
- `checkload.py` — what the machine was doing while a check ran (task
  179): a beat beside the runner reads `verify.RUNNING`, the host's load
  average and memory, and the agent registry, and keeps a bounded,
  sanitized sidecar per verification under `state/evidence/load/` —
  each check's phase (waiting, preparing, running), the other checks
  observed beside it and for how long. Diagnostic only: no runner file
  imports it, so it changes no runner revision, identity or reuse (see
  "Contention: what else was running")
- `acceptance.py` — what a card's requirements have been *judged*
  against: each criterion's state computed from the card's own records,
  bench's evidence and the runs' claims; review rounds and the findings
  carried through fix and recheck; and the three explicit, attributed
  writes (track, decide, a finding event) — never a tick inferred from a
  lane, an exit or a green suite
- `callers.py` — who is calling, at the HTTP boundary: whether a
  request naming a run (`/api/verify`, `/api/events`) proves it with that
  run's own `BOARD_RUN_TOKEN`, whether the `Host` a request names is this
  board on loopback or a host of `BOARD_ALLOWED_ORIGINS`, each in an
  explicit `report` or `enforce` mode; and the bounded, sanitized Host and
  Origin values Settings → Installation shows (see "Callers: run identity
  and Host, reported before enforced")
- `gates.py` — may this merge happen? The operator's requirement per
  guarded mutation, read against evidence bound to the exact commit about
  to move; bench's own review verdict kept apart from GitHub's formal
  state; and an override that records who proceeded over what, and never
  forges a pass
- `required.py` — **Run required checks**: one person's request for the
  candidate commit's missing required checks, in the merge rule's order,
  through `verify.request` and nothing else. Met is skipped, running is
  joined, a failure is not rerun behind anyone's back, the candidate and
  the rule are re-read between requests, and nothing is retried or
  scheduled by itself
- `agreements.py`, `execowner.py`, `orchestrator.py` — the scoped
  orchestration engine (task 137): the operating agreement and its
  effective reading (authority intersected from Bench to phase scope,
  capacity as global pools with non-reserving scope ceilings — the
  legacy per-phase allocation read until a person migrates it (task
  182) — D7 defaults written out,
  draft → preview → apply under revision guards, pause, exclusion and
  hand-back); the one designated execution installation and its OS lock,
  handed over only after quiescence and adopted explicitly, never by a
  heartbeat; and the deterministic `authorize` and public `reading`
  (`GET /api/orchestration`). Absent agreement means off; reading
  launches nothing (see "Orchestration: scopes and operating agreements")
- `operations.py` — the durable operation ledger (task 141): one
  versioned record per orchestrated side effect, written as an intent
  before it and updated with the observed receipt after; which statuses
  reserve a slot and which spent a start, so a restart, a pause or a lost
  reply resets no budget. Imports `config` and `records` only
- `decisiontrace.py` — the orchestrator's decision trace (task 184): one
  versioned, bounded, installation-local record per scheduling pass —
  what each candidate was read as, the rule and reason, what was
  selected and what became of it — with identical idle passes compacted
  losslessly, segment retention and its boundary named, and recording
  failures counted rather than raised. Imports `config`, `records` and
  `redact`; reading it writes and launches nothing (see "The decision
  trace: why it acted, and why it did not")
- `movements.py` — who last moved a card, and whether that makes it this
  board's to orchestrate (task 138, D1): each move's provenance read back
  from the `Bench-Move` trailers `taskfiles` writes on a move's commit and
  the machine-local journal of uncommitted moves, the board's automated
  moves skipped, ambiguous or unrecorded history refused with a remedy,
  and a phase member answered from its phase card's move. The one
  resolver `orchestrator.authorize` and `GET /api/orchestration` both ask
  (see "Last mover, not last editor")
- `decisions.py` — the card keeps everything (task 140, D11/D12): the
  owner's questions, their answers, reasoning responses, blockers,
  recovery attempts and actions as bounded, versioned records in the
  card's `## Orchestration record`, read back field by field from the
  card alone, and the two writes a person makes (answer, withdraw),
  each bound to what the page showed. Imports `config`, `redact` and
  `taskfiles`; reading it sends and launches nothing (see "The card
  keeps everything: decisions and public history")
- `brain.py` — the Brain, read (task 157): the version 1 entry contract
  for the Markdown files in the work root's `brain/`, the index and the
  single-entry resolver, every refusal (a link, a traversal, a duplicate
  id, another contract version) and the revision every write is held to
  — a digest of the bytes read. Imports `config` and `taskfiles`; it
  writes, commits, fetches and launches nothing (see "The Brain:
  maintained entries, read")
- `brainreviews.py` — reviews in the Brain (task 168): every PR review
  round and relevance review filed on a card, in a stage or the archive,
  read with acceptance's own readers and `rv-`/`rl-` identities and shown
  at `/brain/r/<card>/<review>`, with what bench recorded when it filed
  the report and the review's classification record under
  `brain/reviews/` (Unclassified where none exists). Imports
  `acceptance`, `brain`, `config`, `evidence`, `reviews` and `taskfiles`;
  it writes nothing — `brainwrite` writes the records (see "Reviews in
  the Brain")
- `doctor.py` — what this board would grant, run and refuse, asked
  without paying for a model: the same resolver a launch rides, read at
  four honest distances, with every diagnostic's remedy and a coverage
  table that says which distances were *not* taken
- `commands.py` — local commands: a project's chores run against a
  task's worktree
- `events.py` — ingests NORMALIZED events (the adapter contract), session registry
- `drive.py` — runs the project driver, tracks the one live drive; owns
  scrubbing a drive's log (`scrub`), and calls what composition
  registered with `on_ended` when a drive's outcome changes
- `sync.py` — origin/main as the shared board: push on move, pull on a beat
- `changes.py` — the change-request lifecycle (task 116 renamed it from
  `github.py`): opening or adopting a PR, the external-review request,
  the poll and its fold into one verdict, the persisted observation, and
  the owner accessors the layer above asks about a card's PR through. It
  speaks no host shape: it asks `githost`
- `brainwrite.py` — the Brain, written (task 158): a person's create,
  edit, classify, override and retire of one entry file under `brain/`,
  each bound to the revision the page read and to a request identity
  journalled before the file is touched, committed through
  `taskfiles.commit_work_file` and published by `sync`; and each write's
  saved / committed / shared state read again. Imports `brain`,
  `acceptance` (the actor), `taskfiles`, `sync` and `records`; it
  launches and fetches nothing (see "Writing the Brain")
- `reportfiling.py` — filing a run's closing report (task 169): one
  durable, machine-local operation per report, recorded before the first
  append with its destination, run, heading (a review's identity) and
  exact block; idempotent on the card's text, refusing a conflict, a
  missing or doubled card and any other board; the commit, push and index
  read apart; a person's retry and the start-up reconciliation; and the
  Brain's machine-local review index, rebuilt only on request. Imports
  `acceptance`, `brainreviews`, `taskfiles`, `sync` and `records` (see
  "Filing a report, and recovering one that was not filed")
- `completion.py` — merge & clean up: the merge (locally, or on origin
  through `changes`), its publication confirmed on the remote, the
  recovery record a retry finishes from, the card's cleanup and a
  phase's sweep, and the delivered record (it imports `drive`, to park a
  drive before merging, and `changes`, which is why it sits above both)
- `stacks.py` — the derived stack reading (task 117): for a card whose
  `**Base branch:**` names another card's work branch, its parent, the
  cards stacked on it, its place on its path (`2/3` along a chain, `step
  2` where the stack branches), its selected base, review target and
  landing target, and each edge's state in the server's words — from
  local refs, the recorded plans, the PR snapshots and the delivered
  records only. The graph itself is `taskfiles.weave_stacks`; a read
  fetches, writes and runs nothing (see "Stacked cards")
- `stackactions.py` — the three explicit stack actions (task 119), each
  a person's confirmed click bound to the identities its sheet showed:
  **Retarget onto <target>** (on the host only where `retarget` reads
  `available`, otherwise the exact manual step), **Update from
  <parent>** (an additive merge in the child's worktree) and **Finish:
  clean up** (after the remote application base is seen to hold the
  work). Beside `delivery`; only `routes` imports it (see "Maintaining a
  stack: three confirmed actions")
- `delivery.py` — one delivery status per card, computed server-side
  from the authorities above: what is happening, the one next action and
  who holds it, and the merge requirements that apply at the current
  commit. It owns no policy and no enforcement, and a read of it fetches,
  launches and writes nothing (see "Delivery status: one answer per
  card")
- `results.py` — a card's **Show the result** (task 133): the `## Show
  the result` request in its brief (Off, Requested, Required for
  handoff), its kept outputs read against the current commit, the checks
  bench ran, acceptance counts and what is known of merged, installed and
  deployed; the prompt paragraph a run is given, and the one hold — an
  agreed Required whose output is missing, at the move into review
- `cycletime.py` — a card's **Time on this card** (task 165): its lead
  time, stage timeline, each run's span, the checks' union and sum, a
  phase member's integration and delivery waits, what is in flight, and
  every unknown and clock anomaly — read from the move history, the runs'
  progress records, the evidence and the phase log, with nothing written
  (see "Time on this card: cycle time without a second collector")
- `agentsettings.py` — Settings → Agents: the harness and model each
  purpose rides and the local prompt overrides, read from the one
  launch-time resolver and written only as the agent keys of
  `local/.env` and four named templates, with conflict detection,
  backup, rollback and a sanitized history
- `settings.py` — how this board is configured, assembled from the
  modules above and nowhere else: it names the states so nothing reads as
  a pass, compares two resolutions so a change can be previewed, and
  writes one file. No second parser, by construction
- `setupbase.py` — what every setup surface stands on (task 122): the
  portable setup decisions record and the machine-local journal, request
  identity, the revision and integrity guards every setup mutation is
  held to, the actor, and the coding agent's area reading
- `appinspect.py` — what the application's files hold that bears on
  setup, read file by file and running nothing: present, absent or
  `unknown`. Imports `config` only
- `agents.py` — headless work/review jobs, launched through the adapter;
  its public launch primitive (`launch`, `pick_name`, `finish`) is what
  `refine` rides
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
- `setuptasks.py` — setup cards from discovered gaps: what inspecting
  the application's files found absent, previewed exactly, created
  idempotently by stable gap identity, and started only through the
  normal start-work path (so it sits right of `agents`, which it calls,
  and of `previews`, whose configuration says a preview gap is covered)
- `setupguide.py` — the project setup guide: six areas read from the
  authorities above, eight status words and no percentage, and the only
  two writes it owns — a person's decision about an area and a proposed
  setup card — held to a revision and a request identity. It composes
  `readiness`, `previews` and `setuptasks`, so it sits right of all three
- `phases.py` — a phase run: its own branch, its cards merged into it one
  at a time. A beat, not an agent; it holds no registry of where a phase is
- `phasescope.py` — a phase run by its scoped sub-orchestrator (task
  143, D6): explicit adoption from the phase runner (refused until it is
  quiescent), which member may start under the phase's strategy, and the
  phase branch cut, a member integrated and the phase finished through
  `phases`' own mechanics, each with an intent and a receipt. It launches
  nothing (see "Phase scopes: one scheduler per phase")
- `refine.py` — refining a card on the board: comments, the one
  refinement job a person asks for, its validated proposal, and the
  explicit Apply or Discard. The brief and the decisions live in the
  card; this holds only machine-local job state
- `reasoning.py` — orchestrator reasoning (task 139, D2): one bounded
  `reason`-intent coding-agent job per need and input binding, its
  context (card, phase, dependencies, bench's facts) pinned and granted
  by exact reads, and its one public, versioned response validated —
  identity, scope, evidence references, bounds and binding — before it
  is kept. It executes nothing a response proposes (see "Orchestrator
  reasoning: launched agents, public responses")
- `braindraft.py` — the Brain Agent (task 175): a person's request to
  write or update one Brain entry, one bounded `brain`-intent run per
  explicit Generate or Continue, its work-root sources pinned and granted
  by exact reads, its follow-up questions or proposal validated and its
  citations re-read, and the person's Apply written through `brainwrite`
  under the destination's revision and the sources' bindings. It writes
  no entry itself (see "Drafting with the Brain Agent")
- `coordinate.py` — delivering a card in review (task 142; a phase
  scope's members and final head, task 143):
  its required checks, an independent review once the reviewer's
  resolved model is shown to differ from every implementer's, a bounded
  repair, and a delegated merge under the merge rules you configured,
  pinned to the exact head; uncertain merges reconcile against the
  delivered and recovery records and the remote (see "Delivery: checks,
  independent review, repair and your merge policy")
- `recovery.py` — work that is not moving (task 144, D5/D9): a run's
  activity read as separate signals (progress update, tool activity, a
  tool call in flight, a check bench runs, its last commit, its process
  and its age), an incident opened only for a stalled or failed run an
  orchestrator launched, at most `recoveryAttempts` read-only diagnoses
  per incident through `reasoning`, the one coordinated restart a
  diagnosis may propose (stop observed first), failure classification
  and the durable circuit breaker (see "Recovery: diagnose, don't kill")
- `scheduler.py` — bounded orchestration passes (task 141): observe run
  endings, reconcile operations left without a receipt, plan by
  dependencies, priority, fairness and declared `**Touches:**` overlap,
  and launch through ▸ start work's own path with
  `orchestrator.authorize` asked again before every side effect. A beat
  that does nothing while no agreement is enabled; its reading is a read
  (see "Scheduling: bounded passes, durable operations")
- `ask.py` — asking the orchestrator (task 181): a person's free-text
  question about a card, a phase or the board, checked before it runs
  (harness, capacity), recorded in its conversation, and answered by one
  `reasoning` run with the `ask` need from bench's own bounded readings;
  conversations read back from cards and the work state's
  `orchestration/conversations/`. It executes nothing an answer suggests
  (see "Asking the orchestrator")
- `watch.py` — 2s disk poller narrating moves made outside the API, and
  the gate that keeps a move a pull applied from triggering anything
- `routes.py` — every HTTP route: its method and path, its request
  class, and its handler, in one literal table the architecture test
  reads
- `httpd.py` — the socket: finds a request's route, applies the door its
  class names and checks the body before any handler runs, refusing a
  browser's write from a page of another build (`stale-page`, task 124);
  the SSE stream and serving the page
- `board.py` — argparse + startup wiring only: it imports every core
  module at the top and loads the selected hosting provider before it
  serves, so a running board executes the code that was on disk when it
  started, and `wire` is the one place a lower module is told about a
  higher one (`drive.on_ended` ← `previews.record_last`)

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

**The card leaves on the reply, not on a reload** (task 128). Nothing is
shown archived before the board says so: the chip stays busy until
`/api/archive` answers. Its answer is the confirmation the page paints
from — the card and the stage it left, the archive count it left behind,
whether git recorded the move (`committed`: `false` is a moved card
whose commit failed, said in an alarm toast; `null` with
`BOARD_COMMIT_MOVES` off) and the **board revision** whose reading will
hold it. The whole-board reading that reconciles everything else is
asked for in the background, and the action does not wait for it.
⌘Z answers the same way and puts the card back where it stood.

Board revisions are what make that safe. Every `board` frame carries the
next revision (`state.broadcast`), `/api/state` carries the revision it
read *before* reading the disk (`boardRev`, with `boardBoot` naming the
process), so a reading holds every change announced at or below its
revision. A confirmed write is laid over any reading older than its
revision and retired by the first that holds it, so a reading sent
before the archive cannot put the card back; a reading from a restarted
board read the disk afresh and holds everything. A frame naming a
revision already painted, or one the reading already out was sent after,
asks for nothing — so an archive's confirmation and its own frame cost
one reading between them, and one archive causes at most two (that one,
or one follow-up behind a reading sent before it; and the watcher's
later frame). A refusal, no answer, or an answer that names no card
paints nothing from the reply: the toast says nothing is known to have
happened, and a reading says what did. A second archive of a card
already on its way joins the first, and ⌘Z waits for every archive in
flight. The timings are in `develop/perf/README.md`.

### On a phone

A phone is for checking where cards stand (task 154), so below 760px the
board is what the page shows first. The header folds the theme, Refresh,
the setup invitation and the agent-commands note behind one labelled
**More** button (marked while a folded chip has something to say); the
connection chip, the configuration check and every warning stay. The
board shows one stage per screen with a **stage strip** naming each stage,
its count and whether a card in it needs attention — read off the faces
that column painted, never worked out again — and a card's detail view
takes the whole screen and gives the board back where it was.

The activity panel starts closed on a phone, and only a person's toggle
opens or closes it: no event, repaint, reading, navigation or rotation
does. A phone's choice is remembered apart from a desktop's
(`bench-log-open-narrow` beside `bench-log-open` in the browser's
storage). The panel has a **Hide activity** control inside it, its grip
resizes by touch, and its height is a wish clamped to the window the
page has now — at least 80px, at most 40% of a phone's or of a window
shorter than 560px (60% elsewhere), and within that always leaving the
board 160px — so an open panel can never
push the bar holding its own toggle off the screen, which is what it used
to do. The page is `100dvh` high where the browser knows that unit, the
height a phone actually shows. A window shorter than 480px (a phone held
sideways) gets the compact header, bar and panel and keeps its columns.
Anywhere else nothing is folded and no strip is drawn.

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
overrides, your `manager/local/` notes and checks, `.env` and state all
survive byte for byte, in either layout. The tool-owned
seed under `manager/core/seed/` is replaced with the rest of core; your
template at the work root is not. No release
published → it says so and changes nothing; developers working on bench
itself update their clone with git instead — and a clone made before
task 78 runs `protect-local.py` from the target revision first (see
"The project's own notes in manager/local/"). Then re-run `install.py`
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

### The project's own notes in manager/local/

`manager/local/` belongs to the project an install manages, and neither
a release nor a source checkout carries a file there except the
`.gitkeep` placeholders. So an unpacked tarball or a pull has nothing to
overwrite. `install.py` copies the two neutral starters, `AGENTS.md` and
its `CLAUDE.md` pointer, from the tool-owned `manager/core/seed/local/`.
It copies each file on its own and only where that file is missing. An
existing one, edited, empty or a link, is never touched, and a second
run writes nothing. The starters name no test framework: a project's
definition of done is the project's to write, in the application's own
`AGENTS.md` or in this file. No check matcher, profile document or gate
is installed with them. `BOARD_AGENT_COMMANDS` defaults to empty and is
filled from the application's own runner when detected (see the next
section).

Bench's own development policy (its definition of done,
`python3 -m unittest`, and the matching Focus `checks`) is not an
installation default. It lives in `develop/operator-local/`, which ships
nowhere and which no install reads. An operator that develops bench
copies it in by hand, as `develop/README.md` describes. Where an older
source checkout left bench's policy in an install's `manager/local/`,
`install.py` names it on every run and leaves it exactly as it is.
Replacing it is the project's decision.

| Path | Owner | What install, reinstall and update do |
| --- | --- | --- |
| `manager/core/seed/local/AGENTS.md`, `CLAUDE.md` | the tool | replaced with the rest of core |
| `manager/local/AGENTS.md`, `CLAUDE.md` | the project | seeded once each if missing, never changed after |
| `manager/local/checks`, `.env`, `profiles.json`, `prompts/`, `driver/`, `commands/`, `adapters/`, `state/` | the project | never touched (`.env` is written once, as below) |
| `develop/operator-local/` | bench's developers | never shipped, never read |

A project may keep its notes in its own git history. A vendored install
committed into the host repository does exactly that, and nothing here
stops it.

**A source checkout made before task 78** tracks bench's
`manager/local/AGENTS.md`, `CLAUDE.md` and `checks`. Through git alone,
the update that stops tracking them deletes an untouched copy and
refuses to pull over an edited one. So the supported update runs a
preflight from the revision being updated to:

```bash
git fetch origin
git show origin/main:protect-local.py > /tmp/protect-local.py
python3 /tmp/protect-local.py            # report; changes nothing
python3 /tmp/protect-local.py --apply
git pull --ff-only
python3 install.py                       # puts protected versions back
```

The preflight judges every file the update stops tracking. Bench's own
version, untouched, is left for git to remove, so bench's policy is not
resurrected, and the starter is seeded in its place. An edit of bench's
version is copied under `manager/local/state/migrations/local-ownership/`
and verified by hash, then put back to the committed version so the pull
can go ahead. `install.py` restores it afterwards. A version the checkout
committed itself is copied and untracked; commit that untracking, then
merge. A link, a conflict, staged and unstaged content at once, a file
edited again after it was protected, or an untracked file where the
update adds one changes nothing and exits 2, naming each decision.
Restoring never overwrites: a different file found in place is kept
beside the protected copy, and both are named. Running any step again
changes nothing more. A vendored or release install has nothing to
migrate.

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
never overwritten, so `--setup` cannot undo a hand-edit. The same
detection, against the application, fills the key when `--app`/`--work`
write the file for an external layout. With no `.env` at all the setting
is empty too: bench's own `python3 -m unittest` used to be the default,
which granted bench's test runner to every project that had not said
otherwise (task 78).

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

Bench's own definition of done is the operator's local policy, not
something bench installs. Its notes and Focus checks live in
`develop/operator-local/`, and the operator copies them into its own
`manager/local/` by hand (`develop/README.md`). Promoting a release or a
pull into the operator never puts them there or takes them away, and no
other install ever receives them.

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
top of `manager/core/web/board.css`.

The board serves both Plex families itself, from `manager/core/web/fonts/`:
every subset the page once linked from Google Fonts (latin, latin-ext,
cyrillic, cyrillic-ext, greek, vietnamese, and Mono's box-drawing set),
the upstream files unmodified, with their licence and a provenance record
(`SOURCES.txt`). So the page makes no request to anyone but the board.
Symbols Plex does not have — `⌖ ◫ ⚖ ⟶` and the like — are drawn by a
system font, as they always were; `tests/fixtures/page-symbols.json`
lists them and `verify/browser_cases/case_fonts.py` checks which glyphs
come from where.

The header has four primary destinations, in this order: **Board**,
**Brain**, **Orchestrator** and **Settings** (task 159; see "Finding
your way: Board, Brain, Orchestrator, Settings"). Under the Board, a bar
of its own holds its two kinds of work — **Tasks** (the kanban) and
**Phases** — and the board-wide activity, Sessions and Focus, which are
also reached in the context of one card or one phase. Seven views in
all:

- **Board** (Board › Tasks) — the kanban, live, and only what you are holding yourself: a
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
- **Brain** — the project's maintained knowledge, read from the work
  root's `brain/`: a library organised by kind and one entry at its
  stable address, with a person's writes — create, edit, classify,
  override and retire (see "The Brain: maintained entries, read" and
  "Writing the Brain"), and its own search, kind and classification
  controls in a sidebar on the left (see "Finding an entry").
- **Orchestrator** — what the Bench scope and its phase scopes are doing,
  what waits on you, and the operating agreement they work under (see
  "The Orchestrator view"). The switcher carries how many decisions wait
  on you and a mark while orchestration is paused or its agreement is
  invalid.
- **Phases** (Board › Phases) — a swimlane each: where the members the
  Board stopped drawing actually live (see "The Phases view: a swimlane
  each"). Its tab carries how many phases are running and a mark when
  one has halted, and the primary **Board** carries the same mark, so a
  halt is learned from whichever destination you are on.
- **Sessions** (Board activity) — a flight recorder per session: a chronological timeline of
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

### Finding your way: Board, Brain, Orchestrator, Settings

The page has four primary destinations, always in this order (task 159):
**Board** (the work), **Brain** (what the project knows), **Orchestrator**
(coordination) and **Settings** (configuration). Exactly one is marked
current (`aria-current="page"` on its own page, `"true"` when you are
inside it), each is a link a new tab can take, and below 760px they keep
one row of full-size targets. Everything else lives under one of them, so
there is always a way back up:

| You are on | It sits under | The way back |
| --- | --- | --- |
| the kanban, `/board` | Board › **Tasks** | — |
| every phase, `/phases` | Board › **Phases** | Tasks |
| one phase drilled into, `/phases/<card>` | Board › Phases › the phase, drawn alone | the **Phases** breadcrumb (every lane), Back |
| a card, `/cards/<card>` | Board › Tasks › the card; Board › Phases › its phase › the card when opened over Phases | the breadcrumb at the top of the detail view, Close or Escape (back to where it was opened) |
| the whole board's sessions, `/sessions[/<session>]` | Board › All sessions | Board |
| a card's or phase's sessions, `/sessions[/<session>]?card=<card>` | Board › Tasks (or Phases › the phase) › the card › Sessions for it | each breadcrumb step |
| Focus, `/focus[/<session>][?card=<card>]` | the same trail, then Focus | the session, then the card |
| the Brain, an entry, `/brain[/e/<id>]` | Brain | All Brain entries |
| a section, a card in it, `/orchestrator[/<section>][?card=<card>]` | Orchestrator | its own navigation |
| a section, `/settings[/<section>]` | Settings | its own sidebar |

**The Board's bar.** Under the header while the Board is selected: the
**Tasks** and **Phases** tabs, a breadcrumb trail whenever you are below
them, and, apart at the end, the **Board activity** — *All sessions* and
*Focus* across the whole board. Those two are not tabs beside Tasks and
Phases and are not drawn anywhere else; a destination never offers a
link to itself (the session list you are on, the phase you drilled into,
the card in its own breadcrumb).

**Sessions and Focus are activity, in a context.** A card's **Activity**
section opens *Sessions for this card* (a phase card: *for this phase
and its cards*) and *Focus on its newest session*; a phase's lane head
has **Sessions** and **Coordinate** (the phase in the Orchestrator, whose
own phase panel answers with *View this phase in Board › Phases*). In a
context the session list shows only the sessions recorded on that card —
on a phase card and its members, for a phase — its heading says whose
they are, every row and the Focus link keep the context, and the
breadcrumbs lead back to the card or phase. Reached from the Board's bar,
the list is the whole board's. A session opened with no context says
which card it recorded and links to that card and its sessions, or says
no card is recorded; it never invents a parent. A context naming no card
here (archived, another board's, two cards sharing the number) says so
and lists nothing in its place; a session that is not here says that.

**Addresses: what moved, and what did not.** No address changed meaning,
and none was removed: `/board`, `/cards/<card>[?tab=…]`, `/phases[/<card>]`,
`/sessions[/<session>]`, `/focus[/<session>]`, `/brain[/e/<id>]`,
`/orchestrator[/<section>][?card=…]` and `/settings[/<section>]` open what
they always opened, with the parent above selected. The one addition is
the optional `?card=<card>` on `/sessions` and `/focus`, named as a card's
address names it (`?card=007` is corrected to `?card=7` in place); without
it, the activity is the whole board's, exactly as before. A malformed
context is said, never dropped for the whole board's. `/phases/<card>`
now draws that phase's lane alone, under its breadcrumb, where it used
to select the lane among all of them. Back, Forward and reload restore
the visited context, and following any of these links reads; it writes,
fetches from a remote, launches and runs nothing.

### Finding an entry

The Brain's list is a library organised by **kind** (task 183). The way
to browse it sits in a sidebar on the left (task 185), independent of the
Board's stages, and the entries fill the main column to its right — the
sidebar comes first for Tab, as it does for the eye:

- **Search** — every word typed, case blind, against each entry's
  title, summary, id, kind, file name, sources and related ids.
- **Kind** — *All kinds*, which draws one section per kind that has
  entries, in the contract's order (Guide, Investigation, Decision,
  Review, Prototype, Attachment), or one kind alone. Each kind's count
  reads *shown of all entries of that kind*.
- **Show classifications** — one checkbox each. **Current** and
  **Unclassified** are shown by default; **Historical** and
  **Superseded** are hidden by default. Inside a kind each shown
  classification keeps its own heading, so Unclassified never reads as
  current knowledge. Their counts are for the chosen kind and search.
- **Reset to default view** — all kinds, Current and Unclassified, no
  search.

Hidden is not removed. The status line above the list says how many of
the Brain's entries are shown and how many are hidden by the
classification controls, each kind's section says what it hides, and
an entry's address (`/brain/e/<id>`) opens it whatever the view — a
Historical or Superseded entry says it is hidden from the default list
and how to list it. A search or a combination that shows nothing says
what matched and what is hidden, and offers **Reset to default view**
and **New entry** — and, where the controls hide what matched, **Show
hidden classifications**; the default view itself (a Brain holding only
Historical and Superseded entries, say) offers that instead of a Reset
to the view already shown. A kind with no entries says so in its own
words. On a wide window the sidebar stays in view while the list
scrolls. Below 760px the sidebar folds into one **Browse and filter** button
above the list, naming the current view, rather than squeezing the
entries beside it.

It all reads the index the board already sent — deterministic, bounded
by the files `brain.py` reads, no model and no request — and none of it
writes, reclassifies or launches anything. The choices are kept across
a visit to an entry and Back, with focus returned to the entry the
reader came from; they are not part of an address, and a reload starts
from the default. Files that cannot be shown are listed with how to fix
each kind of problem, and are not found by search until they are fixed.
There is no curated handbook, no seeding and no automatic classification
yet: those are later phases, and nothing on the page offers them.

### The page's script: modules under a policy

The page's script is native ES modules under `manager/core/web/`, loaded
from the build like every other asset (task 124). No npm, no bundler, no
framework: the files the board serves are the files in the tree.

- **One side-effectful module.** `board.html` loads `web/main.js` as a
  module and carries no script of its own. `main.js` is the composition
  root: it wires the listeners, applies the theme, reads the state and
  opens the stream. Every other module only imports, declares and
  exports, so importing one starts nothing.
- **Import direction.** `lib/` (`dom`, `time`, `toast`, `keep`, `md`,
  `api`) imports only `lib/`; `words.js` and `app/state.js` import
  nothing; nothing imports `main.js`. Every other edge from `app/` or
  `views/` to anything but those is listed in
  `tests/fixtures/web-imports.json`, a reviewed baseline that may only
  shrink. `tests/test_script_policy.py` holds the page to all of it, and
  `tests/node/board_modules.test.mjs` imports every module but `main.js`
  under Node, so an import that names nothing, or a module reading
  another's binding before it exists, fails there and not in a browser.
- **A console seam.** Modules make no globals. `main.js` puts a short,
  named list of them (`S`, `render`, `loadState`, `toast` and a few
  more) on `window` for the browser suite and a person at the console.
  Replacing one there does not replace it inside a module.
- **The policy.** The page is sent with a Content Security Policy
  (`assets.PAGE_POLICY`): `script-src 'self'` — the build's modules and
  nothing else, no inline script, no `eval`, no handler attribute —
  `connect-src 'self'`, and `'none'` for objects, frames, workers, the
  base and forms. Inline styles stay allowed: the templates write tints
  and heights into `style` attributes, and a style runs nothing. The one
  image is the favicon, a `data:` SVG. A plans/ or reference/ document is
  opened in a tab of its own under its own sandbox policy, or fetched as
  Markdown into the drawer.
- **A page from another build.** Every mutation the page sends names its
  build (`X-Bench-Build`, from `lib/api.js`). After a restart into
  another build, the board refuses a browser's write from an older page —
  `409 stale-page`, nothing changed — and the page shows a banner: it is
  out of date, reload to update, and what a reload would discard (an
  unsent comment, a note, a Settings draft). Only a request that states a
  browser (an `Origin` or `Sec-Fetch-Site`) is asked for a build: the run
  shims, the event bridges and `curl` are never refused for it.

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

**Every card in `review/` says whether its review approved it** (task
152), on a line of its own under the title, whatever the pill says:
being in Review is a stage, not an approval. The words are the server's
(`delivery.review_status`, carried as `delivery[file].review`), read from
Bench's own verdict bound to the commit the card would merge — never from
the lane, GitHub's formal state, a verdict parsed out of the card's text
or a review checkout's label:

| word | label | when |
| --- | --- | --- |
| `review-approved` | Review approved | Bench's verdict at this exact commit, since the newest retarget, is APPROVE |
| `review-changes` | Changes requested | that verdict is REQUEST CHANGES |
| `review-running` | Review in progress | a ◔ review PR run is alive on the card |
| `review-outdated` | Review outdated | the newest verdict is about an earlier commit, or was given before a retarget |
| `review-incomplete` | Review incomplete | the newest review run ended without recording a verdict |
| `review-unavailable` | Review status unavailable | no commit can be named here, or the card's text claims a verdict about this commit that Bench has no record of |
| `review-pending` | Review pending | nothing about this commit yet |

An approval is Bench's review and nothing more: not GitHub approval, not
a person's acceptance, not a passed check and not permission to merge, so
it can sit beside `Check failed` or `Sign-off needed` and the pill still
names the blocker. The line is a button: it opens the card on Reviews,
whose PR group and Overview's Signals lead with the same words. Reading
it runs and writes nothing.

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
head is sticky), and five sections follow as tabs — Tab reaches the row,
the arrow keys, Home and End move along it. The head's three controls
are icons, each named in words for a screen reader and as a tooltip, and
kept together on one row at every width beside the number and status,
with the title wrapping on the full width under them: **Copy link**
(confirmed in a toast), **Expand** / **Narrow** (a wider view for long
briefs and reports; its name, icon and pressed state follow it) and
**Close** (Escape too). Under the tab row, after a deliberate gap, the
card's **Tools** come first on every tab — the same group as its **⋯**
menu, labelled once — and then the open section's text.

- **Overview** — four sections in the order a busy reader asks their
  questions (task 149), then everything else. **The work**: what the
  card is and why, quoted from the brief as it reads now — its opening
  paragraph, the first paragraph of its problem section (`Context`,
  `Problem`, `Background`…), the headings or leading bullets of its scope
  section (`What to build`, `Scope`…) and its boundaries (`Out of
  scope`…), each labelled with the section it came from, clipped at a
  sentence and marked when it continues, with the brief's revision and a
  link to the whole brief; open questions in the brief are raised there.
  **Where it stands · what happens next**: the delivery status — its
  word as a heading, its sentence (announced as an alert only in the
  alarm tone), the one primary action and who holds it, and every other
  legitimate action under **More actions** — a guide, not a wizard:
  nothing in the list is disabled because the primary comes first, and
  an action whose handler belongs to a later card is drawn disabled with
  that reason — then a failed run, the stage, the latest run's progress
  as #73 reports it (agent-reported with its age while the run lives;
  *history, not current* once it has ended; *reporting unavailable*
  where it was never given a channel), the **last result** a run
  recorded (the first paragraph of its newest work report or PR update,
  labelled *the run's own account, not a check result*; a card in review
  or done with none says its result is not stated), the orchestration
  line and, where one is recorded, the **Work base** (which code the
  work starts from, and a held new start kept in view as the blocker it
  is). **What is required**, in two halves: *to do the work* — the
  card's information, its dependencies (each by number, with its stage,
  or *not on this board*) and its criteria — and *to merge it* —
  **Merge requirements**, `1 of 2 merge requirements met for <commit>`,
  one row per required obligation with its state in words and, for a
  problem, its remedy, with **Optional checks and earlier runs** and
  **Technical details** (the commit, the merge rule, the completion, the
  PR as of when it was observed) folded underneath. **Reviews and
  findings**: the findings and the last review round explained against
  its revision and what has happened since, and the brief's own
  relevance review. Then, under **More about this card**, Show the
  result (one line and a fold while nothing is asked for or kept),
  **Time on this card** (see "Time on this card: cycle time without a
  second collector"; its lead time is also one **Time** line under
  *Where it stands*), the stack, the **Signals**
  block — Bench checks (`Required: 1 of 1 passed on <commit>. Optional:
  2 (see ◫).`), external CI, review and the merge requirements as
  separate rows, each in its own words — with the branch plan folded
  underneath. Green CI is labelled
  as not a Bench check, and a board with no gate says that nothing is
  required, which is not a pass. Review is the records it is, each on a
  row of its own: **Bench review** (the durable verdict and the commit
  it was bound to), **GitHub review** (the review objects alone — not
  CI, not mergeability — and *not observed* where bench has not read the
  pull request) and, when it says something the Bench verdict does not,
  **Reported review** (the newest round filed in the card's text, with
  its revision). A verdict about another commit reads *an earlier
  commit, history*, never as today's answer, and none of the three is
  inferred from another.
- **Brief** — the requirement alone: the head's fields as a grid of
  their own, an index of the brief's sections when it has more than two
  (each moves focus to its heading), and the brief, no history; then
  #65's discussion and its refinement controls.
- **Inputs** — the card's required information (see "Required
  information: inputs and tools"). A card that names no input or tool
  says so in one sentence; what readiness does and does not prove, and
  how to write the section, is one fold away.
- **Acceptance** — the criteria and their results; see "Acceptance and
  review findings". An untracked criterion leads with its first clause,
  so a long list scans by its leads.
- **Reviews** — two groups, each with its own empty state, the code's
  first: **PR reviews** (the implementation at a code revision): open
  findings first, each with its fix and resolution chain, then the
  newest round, with resolved findings and earlier rounds folded away;
  then **Relevance reviews** (is the brief still needed and current? —
  see "Relevance reviews: comment, then update the card from one"). What
  each kind is, and is not, is a fold under its group.
- **Activity** — the dated record, newest first: progress updates
  (labelled as the run's), work reports (each folded, whole), each review
  as a dated line linking to the exact review in Reviews rather than a
  second copy, comments and decisions, and acceptance and finding
  records. Raw reads, edits and commands stay in the Sessions view.

**Who owns the summary: nobody but the brief** (task 149). The Overview's
account of the work is not a second text to keep in step. It is read out
of the brief on every read of the card's text (`taskfiles.brief_outline`,
sent with `GET /api/card` as `outline`): verbatim excerpts, never a
paraphrase, never persisted, and never asked of a model — opening a card
launches nothing and writes nothing. So it cannot go stale: an edited
brief is a new outline, and a new revision, on the next read; a report or
review appended to the history changes nothing about it, so an old work
report never becomes the requirement; and while a newer text is being
read the summary says it is not current. To change what the Overview
says, edit the brief — its opening paragraph first. A brief with none of
the parts above to quote (a legacy card, a title and a checklist) says
so, and nothing is written in their place; the title is never offered as
the summary. The last result is the same kind of excerpt, of a report
the run wrote, and is labelled as the run's own account.

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

**The board lists cards; a card's text is read when it is opened**
(task 129). `/api/state` carries every card's fields — identity, stage,
headers, phase and stack membership, the verdict and assignee read out
of its text — and its revision (`rev`, the first 16 hex digits of the
text's sha256), but not the text itself: every server section that reads
a card's text (delivery, acceptance and findings, verdicts, phase
progress) is computed from the full board before the list is made, so no
signal is lost and the page infers nothing from what is left out. A
phase card's log travels as `phaseLogs`. Opening a card asks `GET
/api/card?file=&stage=` for its text, where it splits and its revision.
The answer is about that name in that stage only: a card that has since
moved reads `409 moved` with the stage it is in, an archived one `410
archived`, a deleted one `404 gone`, and the page then reads the board
again so the view follows the card or closes. The text is keyed by card
and asked again when the listed stage or revision moves, through the
same race policy as the other on-demand readings — a late reply never
lands on another card or over a newer one — and the brief says
*Reading the card…* (announced as busy) or why it could not be read,
with **Try again**. `/api/tasks` still serves every card's whole text.

A live update keeps your place: each section keeps its own scroll
position, open folds and unsent text survive a redraw, and a record
arriving above what you are reading moves the page rather than you.
Cards are reachable by keyboard. Tab lands on a card, Enter or Space
opens its detail view, focusing a card shows the same actions as hovering
it, and closing the detail view returns focus to the card. Board and
Phases draw the same card.

**A card is redrawn only when what it shows changed** (task 130). The
card builder writes a card as markup first (`cardSpec`), and that markup
— with its actions and their armed or busy windows — is the card's
signature. A paint keeps every card node whose signature is unchanged,
moves one whose place changed, replaces one that changed and removes one
that left (`lib/keyed.js`); the Board's columns and each phase lane are
built once and kept. So a session event, or a reading that changes one
card, leaves every other card's node — its focus, its open tools, a drag
in flight — exactly as it was, and a replaced card takes focus with it.
At 1000 cards one changed card is visible in about 25 ms where it took
about 300 ms (`develop/perf/README.md`). The first paint still builds
every card; nothing is windowed or left out of the page.

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

## The Brain: maintained entries, read

The **Brain** is the project's maintained knowledge: explanations,
investigations, decisions and guides, each a Markdown file in
`<work root>/brain/` with a stable address (task 157). Any person who can
reach the board can write and classify them there (task 158, "Writing
the Brain"), and find them with its search and filters (task 159,
"Finding an entry"). The reviews filed on cards are read there too, at
addresses of their own, without being copied (task 168, "Reviews in the
Brain"), and a report whose filing failed is recovered from there or from
its card (task 169, "Filing a report, and recovering one that was not
filed"). What it does not do yet, said so nobody looks for it: a
curated handbook, seeding a Brain from an application, adopting
`reference/` files, and any agent classification. A run is given no
Brain entry because it exists: being in the Brain is not being approved
context. The one agent that reads the Brain is the **Brain Agent**, which
a person asks to draft one entry and whose draft a person applies (task
175, "Drafting with the Brain Agent").

### Where entries live

`brain/` sits beside `tasks/`, `plans/` and `reference/` in the **work
root** — the install in a vendored or source-hosted layout,
`BOARD_WORK_ROOT` in an external one — and is committed to the work
repository like the cards. Install, reinstall, update and every reading
leave it alone, and a board with no `brain/` works exactly as before,
its Brain saying what fills it. The first entry saved through the board
creates it — that explicit write, and nothing else. An entry may also be
written by hand and committed like any work-state file; it appears on
the next reading, committed or not.

### The record, version 1

```markdown
# How review reports stay on their own board

**Brain:** 1
**Id:** e-report-routing
**Kind:** Guide
**Classification:** Current
**Summary:** A report is filed on the card its run was launched for.
**Source:** #148, plans/148-report-isolation.md, https://example.com/notes
**Related:** e-report-isolation

The body, in Markdown.

## Classification history

- 2026-11-19 · Unclassified · ronald — written before the fix landed
- 2026-11-20 · Current · ronald — explains the shipped routing
```

| Field | Required | Value |
| --- | --- | --- |
| `# Title` | yes | the first line; free to change |
| **Brain** | yes | the contract version, `1` |
| **Id** | yes | the stable, opaque id: `a-z`, `0-9` and `-`, 2–64 characters. Allocated once, never reused |
| **Kind** | yes | `Review`, `Investigation`, `Decision`, `Guide`, `Prototype` or `Attachment` |
| **Classification** | yes | `Current`, `Historical`, `Superseded` or `Unclassified` — exactly one |
| **Summary** | no | one line for the list |
| **Source** | no | where it comes from, comma-separated: `#<card>`, `plans/<file>`, `reference/<file>`, a web address, or plain text |
| **Related**, **Supersedes**, **Superseded by** | no | other entries, by id |
| **Invalidated** | no | on a Superseded entry with no successor: the decision that its claims no longer hold |
| **Override** | no | a **manual override**: `manual · <who> · <when> — <reason>`. A person fixed this classification; later automation must respect it until a person clears it |
| **Removed** | no | makes the entry a **tombstone**: kept, said to be removed, with this reason |
| **Replaced by** | no | the entry that took a tombstone's place |

**Invalidated** and **Override** were added by task 158 within version 1:
a reader that predates them shows them as fields it ignores, and nothing
else changes.

The file's name is free (a flat name ending `.md`): **identity is the
`**Id:**` line**. Renaming the file, changing the title or changing the
classification keeps the id and the address `/brain/e/<id>`.
Classification and kind are separate facts, and so is where an entry
came from. A Current entry is not authoritative because it is Current: an
entry explains, and never overrides the code, the configuration, a merge
rule or a person's decision.

**Where the header ends.** The header is the title, then one block of
field lines with no blank line inside it. It ends at the first blank line
or the first line that is not a field, and everything after it is body —
so a body that opens with a bold lead-in such as `**Note:** …` stays
body and is never read as a field.

The `## Classification history` section records who classified the entry
what, when and why, one line each, oldest first. It is read only as the
entry's **last** section: its heading counts outside a code fence
(```` ``` ```` or `~~~`) and only when no other `#` or `##` heading
follows it, so an entry that quotes this format in a fence, or has a
section after one, keeps its whole body. The header's
**Classification** is what is shown; a newest history line that
disagrees, a Superseded entry that names no successor, a missing summary
and an id named in a link that no entry carries are each shown as a note
on the entry, never fixed up.

### What a reading does with each file

| The file | What the Brain shows |
| --- | --- |
| a valid version 1 record | the entry, in the list and at its address |
| one carrying **Removed** | a tombstone at its address, saying what replaced it; never an unknown id |
| two files with one **Id** | neither: the address names both files until one changes |
| a version 1 record that breaks the contract (a missing field, an unknown kind or classification, a field written twice, not UTF-8) | a diagnostic naming the file and what is wrong |
| another **Brain** version | `unsupported`: listed, never interpreted |
| no **Brain** line | not an entry, said so |
| a link (the directory itself included), a FIFO, a name that is not flat, a file over 256 KiB | refused, unread |
| `brain/reviews/` | not an entry: the review classification records (see "Reviews in the Brain") |
| anything not ending `.md`, or hidden | skipped; reserved for later versions |

An id nothing carries reads **No entry … on this board**: it may belong
to another board, or was never written. A traversal is not an id at all.

**Versions and migration.** The contract is versioned so a reader never
guesses. A file declaring a version this board does not read is listed
and left alone. A later version will ship its reader with it; where
records must change, the change is an explicit write through the board's
write path, previewed and committed — never a rewrite on read, and never
a second record store.

### Reading changes nothing

`GET /api/brain` (the index) and `GET /api/brain/entry?id=<id>` (one
entry: `200`, or `404` for an unknown id, `409` naming the files for a
duplicate, `400` for something that is not an id) read `brain/` and
nothing else. They create no directory, ask git nothing, fetch nothing,
run no project code or check and launch no agent. A **revision** is a
digest of the bytes read — an entry's of its file, the index's of every
file — so an uncommitted edit is a new revision and git's HEAD is never
the question. Every write is held to these revisions (below). There is
no stored index to go stale: every reading is rebuilt from the files.

An entry's metadata and its body come from one reading of one file. Its
text is untrusted: the body goes through the page's Markdown renderer
(raw HTML stays text, a link is followed only to the web, mail, an anchor
or a relative path, an image is a link), and every other string is
escaped. A relative link in an entry resolves against its own address,
so `[the investigation](e-report-isolation)` opens that entry. A
`plans/` or `reference/` source opens through the sandboxed `/files/`
route; a web source is shown as a link and never fetched.
`/files/brain/…` is not a route: entries are read through the Brain only.

### Writing the Brain

Anyone who can reach the board can write entries (task 158). That is the
board's existing access boundary and nothing more: there is no owner-only
approval and no new authentication. Every write is attributed to this
board's git identity in the work state (the operating-system user where
git has none), never to a name in the request.

**The workflow.** On the Brain's list, **New entry** opens a form: title,
kind, a one-line summary, sources (one per line), related entries (ids),
the body in Markdown with **Write** and **Preview**, and the starting
classification with its reason. **Save** writes the file and opens the
entry at its new address; **Cancel** writes nothing. On an entry:

- **Edit** — title, kind, summary, sources, related entries and body.
  The id, the classification and its history are kept, and so are header
  fields this version does not read.
- **Classify** — Current, Historical, Superseded or Unclassified, with a
  required reason. Each classification, and each correction of one, adds
  a line to the entry's `## Classification history` with who, when and
  why; the earlier lines stay. **Current** may mean an accepted
  investigation that is not built yet — it is never a claim that the work
  shipped. **Superseded** needs the replacing entry, which must exist, not
  be retired, not be this entry and not lead back to it through its own
  **Superseded by:** chain; or an explicit **invalidated, with no
  replacing entry** decision and its reason. Choosing any other
  classification clears both.
- **Set override** / **Clear override** — records, with a reason, that a
  person decided this entry's classification (`**Override:**`). Agent
  classification is later work; when it comes it must leave an entry with
  an override alone until a person clears it. The override grants nothing
  else.
- **Retire** — armed, then **Confirm: retire this entry**: the file is
  kept as a tombstone (`**Removed:**`, optionally `**Replaced by:**`), its
  address keeps saying what happened, and its id is never reused. A
  tombstone is not edited further.
- **Revisions** — the work-state commits that touched the file (read from
  local git, nothing fetched) and this board's own saves of it.

Editing is a mode of the page, not an address: following a link, Back
and Forward read and never write, and leaving the entry puts an unsaved
draft away.

**Exact revisions; nothing is overwritten.** Every write but a create
names the revision the page read. If the file is anything else — another
person's save, a hand edit, a teammate's change a sync brought — it is
refused with `409`, nothing is written, and the page shows **what you
tried to save** beside **what is on disk now**. Your draft stays.
**Use the current version and keep my draft** takes the current version
as the new base so you can bring your change across and save again;
**Discard my draft** drops it. Nothing retries a refused write.

**One operation per request.** Every write carries a request identity.
Its intent — the id allocated, the bytes about to be written, the
revision they replace — is journalled durably under
`manager/local/state/brain/operations.json` (machine-local) before the
file is touched. The same request again (a double click, a reply lost on
the network, a restart part way) answers from the record or finishes the
same write, so it never makes a second entry or a second history line;
the same identity with a different payload is refused. When a Save gets
no answer, the page asks the record before saying anything happened.

**Saved, committed, shared — three facts, each read again.** The page
shows each one after a write:

| what it says | what is true |
| --- | --- |
| **Saved** to `brain/<file>` | the bytes are on disk at that revision |
| **Committed** `<sha>` | a `board: brain <id> …` commit in the work-state repository touching only that file (unrelated staged and untracked work is left as it is) |
| **Not committed** | `BOARD_COMMIT_MOVES` is off: commit it yourself |
| **Commit failed** | the bytes are saved and kept; nothing was rolled back. **Retry commit** commits them while the file still holds exactly them |
| **Push pending** | committed; sync has not yet put it on the remote. **Push now** asks sync once |
| **Push failed** | committed here and not shared, with sync's own reason; **Retry push** |
| **Not shared** | `BOARD_SYNC` is off |
| **Shared** | the remote-tracking branch holds that commit, or those exact bytes |

**What a write may touch, and what it refuses.** One flat-named file
directly under `brain/`, through the work state's own commit and sync
path — never a card, a review, a verdict, the application, the
configuration, a gate or an AGENTS file. Refused with nothing written: a
`brain/` or entry file that is a link, not a directory or not a regular
file, or that resolves outside the work root; an entry another file
also claims, or one that breaks the contract (fix it by hand); a line
break, a control or direction-changing character, or `<`/`>` in a title,
summary, source or reason; a title starting or ending `#` (Markdown
would read the trailing `#` away); a source with a comma,
credentials in a web address, or a `javascript:`/`data:`/`file:`
scheme; a body with its own `## Classification history` heading or an
unclosed code fence — and a classify, override or retire of an entry
written by hand whose body has one, since the history line would land
inside the fence (close it first); a related, replacing or superseding id that is not
an entry here. Sources are text: a web address is never fetched, and
nothing in an entry grants a run any permission.

| Route | Does |
| --- | --- |
| `POST /api/brain/create` | `{request, title, kind, summary?, sources?, related?, body?, classification?, reason?}` — a new id, and `brain/` if it is missing |
| `POST /api/brain/edit` | `{request, id, expected, title, kind, summary?, sources?, related?, body?}` |
| `POST /api/brain/classify` | `{request, id, expected, classification, reason, supersededBy? \| invalidation?}` |
| `POST /api/brain/override` | `{request, id, expected, action: set \| clear, reason}` |
| `POST /api/brain/retire` | `{request, id, expected, reason, replacedBy?}` |
| `POST /api/brain/retry` | `{request, action: commit \| push}` — the half that did not happen |
| `GET /api/brain/operation?request=` | one write's state now |
| `GET /api/brain/revisions?id=` | the entry's commits and saves |

The POSTs sit behind the browser-mutation door like every person's
write. None launches an agent, runs a check or fetches anything.

**Recovering.** A conflict: read both versions, choose, save again. A
failed commit: the file is saved; fix what git said (a held
`index.lock`, say) and **Retry commit**, or commit it by hand. A failed
push: fix the remote, then **Retry push**, or let sync's next beat do it.
A restart mid-write: send the same request again — the page does — and
the journal finishes it.

### Drafting with the Brain Agent

Nobody should have to write the Brain's articles by hand (task 175).
**New entry** asks what the entry should capture — *Explain how
verification works*, *Capture this architecture decision* — and an
existing entry offers **Ask Brain Agent** beside **Edit**: what to
correct or add. A launched, read-only coding agent reads the project,
asks follow-up questions only when it must, and proposes **one** entry.
A person reviews it, edits it and applies it. Writing the entry yourself
stays one click away (**Write it yourself instead**), and **Edit** works
exactly as before.

**The journey, and what each step costs.**

| step | what happens | launches | writes |
| --- | --- | --- | --- |
| open the composer, type | the request and the preflight below | nothing | nothing |
| **Generate** (armed) | round 1: one bounded `brain` run | one run | the private request |
| the agent asks | *Waiting for your answers* — a waiting state, never a running or failed one | — | — |
| type answers | kept on the page | nothing | nothing |
| **Continue** (armed) | the next round, with your answers or what to revise | one run | the private request |
| review | the draft rendered, its exact changes against the entry it read, its sources with their revisions, its claims, what stays uncertain | nothing | nothing |
| edit | title, kind, summary, sources, related entries, body; for a new entry, the classification (the agent's is only a suggestion) | nothing | nothing |
| **Apply** (armed) | the final, human-edited entry through the Brain's own write | nothing | one entry |
| **Discard**, **Cancel** | the request closes; the Brain is unchanged | nothing | the private request |

A request is at most `BOARD_BRAIN_DRAFT_ROUNDS` rounds (4), each at most
`BOARD_BRAIN_DRAFT_TIMEOUT` seconds (900); on the last round the agent
must propose. Reaching a limit is said — *the round reached its limit*,
*this request has used its rounds* — and nothing is relaunched by
itself. A board that restarts closes a round in flight as failed, naming
the restart; **Continue** is the person's way on. A round that fails
after an earlier round proposed a draft — a revision that hit its time
limit, crashed, declined, answered invalidly or was cut by a restart —
does not take that draft away: the request stays *Draft ready*, the
failure is shown above the draft, and the draft (with any edits on the
page) can still be applied, revised again or discarded, at the round
limit too. Only a request with no draft yet reads *Failed*.

**What it reads.** Before Generate, the composer's fold says exactly what
the run would use (`GET /api/brain/draft/preflight`, a read):

- **The application checkout**, the one `BOARD_APP_ROOT` names (else the
  repository the install sits in) and its commit. The run starts in it
  and reads it with the vendor's read-only tools. One application root is
  configured on a board; bench reads no other directory as the
  application, and says so.
- **The work root**, as a bounded, deterministic selection: Brain entries
  (60), cards in the five stages, newest first (80), `plans/` (30) and
  `reference/` text (20), at most 512 KiB each and 6 MiB in all — each
  copied read-only and granted by one exact read, so the run reads the
  bytes bench hashed. A link, a file resolving outside the work root, one
  over a bound, one that is not UTF-8 text and a credential-shaped name
  (`.env`, `*.pem`, `*token*`, `*secret*`, …) are left out, and the
  preflight lists each with why. Archived cards are not pinned.
  **Where the work root sits inside the checkout** — a vendored or
  source-hosted install — the run can also read it through the
  application, so this selection bounds what is pinned and what may be
  cited (`work:`), not what is readable; there the deny rules below are
  the boundary, and they add the work root's `tasks/archive/`. The
  preflight says so.
- **Never**: credential-shaped paths in the checkout (`**/.env`,
  `**/.env.*`, keys, `.git/`, `.netrc`, `.npmrc`, `.pypirc`, and the
  shapes bench refuses to cite — `*secret*`, `*credential*`,
  `*password*`, `token`/`tokens` and `*_token*`-style names; a glob errs
  towards denying, so `secretscan.py` is unreadable too; each is denied
  in its plain spelling and again case-blind, `**/.[eE][nN][vV]`, since
  bench refuses to cite `Secrets.yaml` or `.ENV` case-blind), the tool's
  `manager/local/.env` and preview secrets, and every directory of
  `manager/local/state/` but `state/brain-context/`, where a drafting
  round's own copies are pinned. `state/context/`, where every other run
  pins, is denied as one directory, so a bundle another run makes while
  the round reads is denied too; other drafting rounds' bundles (which
  hold only work-root text a drafting run may read) are denied one by
  one. A deny path the adapter cannot write as a rule — an install under
  a path with a space, say — refuses the preflight and the launch; no
  rule is ever dropped. These are read deny-rules the adapter renders;
  **an operator's ability to read a file is not proof the run can, and a
  permission rule is not an operating-system sandbox**.

**What it may do.** The `brain` intent is the `reason` stance: no
worktree, no command grant, no check, no shell, no edit tool — and no web
access (`WebFetch` and `WebSearch` are withheld too). A profile document
that gives it commands, checks or preparation steps is refused
(`intent-restricted`), and an adapter that cannot deliver pinned context
by exact reads — opencode — refuses it by name (`intent-unsupported`)
before anything is written or paid for. Its model is
`BOARD_AGENT_MODEL_BRAIN`, else the default; Settings → Agents shows the
effective harness and model, and the prompt is the **Brain drafting**
template (`brain.md`), editable there like the others. The Configuration
check lists it as **Brain drafting**.

**What it answers, and what bench does with it.** One JSON object between
`<<<BRAIN` and `BRAIN>>>` (or `BRAIN DECLINED:`): `questions` (at most
five), or one `proposal` — title, kind, summary, body, a suggested
classification (`Current`, `Historical` or `Unclassified`) and why, the
sources it cites, its claims each marked `implemented`, `deployed`,
`agreed-future`, `unresolved` or `unknown`, and what stays uncertain.
Bench validates it, field by field, against the round it was launched
for:

- every citation is re-read and bound now: `work:<path>` must be one of
  the sources the round was given (its pinned hash), `entry:<id>` a Brain
  entry the round was given, `app:<path>` a regular file inside the
  checkout — never a credential-shaped path, never bench's private state,
  never one that climbs out — hashed with the checkout's commit;
- `implemented` and `deployed` claims cite a bound source; deployment
  nobody recorded stays `unknown`;
- an update is about the entry asked for and no other; a new entry may
  only *suggest* an existing one (`suggestTarget`), which a person
  confirms with **Update … instead** before the next round drafts the
  update;
- the entry itself passes the Brain's own write rules
  (`brainwrite.check_fields`); an unknown field, an HTML comment and a
  `## Classification history` heading are refused.

A proposal that fails any of these reads **Failed**, with each problem,
and nothing was written. A valid one gets a **Sources and claims**
section appended to its body by bench — each claim with its status, each
source with the commit and hash read — which a person may edit like the
rest. Everything a source says is data: an instruction inside a card or a
file grants no tool and cannot reach Apply.

**Apply is the only writer.** It sends the person's final fields, bound
to the newest draft round and to a request identity, and bench:

1. reads every cited source again — one that changed or went is a
   **conflict** (`source-drift`), nothing written: ask for a revised
   draft so the agent reads them again, or discard it;
2. validates the edited fields by the Brain's own rules;
3. writes through `brainwrite.write` — `create`, or `edit` held to the
   revision the round read, so a hand edit, another person's save or a
   synced change since is a conflict (`destination-changed`), never an
   overwrite. An update keeps the entry's id, classification, override
   and history; change the classification afterwards with **Classify**;
4. reuses the Brain's request journal, so a double click, a lost reply
   or a restart part way writes one entry and one history line. An Apply
   whose outcome was not recorded is settled from that journal at the
   next Apply and at start-up.

Saved, committed and shared are then shown on the entry exactly as for a
person's own write ("Writing the Brain").

**Progress, and what is kept.** While a round runs, the request shows
what is done (sources pinned, the run launched, its model), what is
happening now, what remains and what it waits on — bench's observations —
and, apart from them, the run's last tool activity (*activity, not
progress*; the run has no shell to report with). The request, its public
conversation (your request, answers and revisions, the agent's questions
and summaries, the board's outcomes), the validated proposal and its
bindings are kept under `manager/local/state/brain/drafts/` —
machine-local and never committed — so a reload or a restart resumes the
request. The run's log, manifest, prompt and pinned copies stay in the
run's own records and are never sent to the page. **Brain Agent
requests** on the Brain's list reopens one.

| Route | Does |
| --- | --- |
| `GET /api/brain/draft/preflight?target=&text=` | what a Generate would read and launch; reads only |
| `GET /api/brain/drafts[?id=]` | the requests, or one, as the page reads them |
| `POST /api/brain/draft/start` | `{request, text, target?, expected?}` — Generate |
| `POST /api/brain/draft/continue` | `{id, request, round, answers?, note?}` — Continue, Revise, Try again |
| `POST /api/brain/draft/retarget` | `{id, round, target}` — switch a new entry to an update; launches nothing |
| `POST /api/brain/draft/apply` | `{id, request, round, title, kind, summary, body, sources, related, classification?, reason?}` |
| `POST /api/brain/draft/cancel`, `/discard` | `{id}` |

They sit behind the browser-mutation door. **What is proved, and what is
not:** `tests/test_braindraft.py` runs the whole lifecycle through the
Claude adapter's real `run` against a stub vendor, on a split fixture
(an application repository, a work root and the tool's state apart) and
on a vendored one (all three inside the checkout), and holds the
generated permission text. Whether the installed CLI honours those rules
with a real model is the opt-in, billable
`develop/brain-agent-probe.py --approve-real-agent`, which nothing runs
by itself; a passing stub run is not that proof. The probe reads the
vendor's own tool events (its PostToolUse hook, posted to a listener in
the board's place) and fails on any successful Read of a withheld file.
It cannot tell a Read the vendor denied from one the model never
attempted — a denied call fires no hook — and says so in its report.

**Not in v1:** background or card-triggered curation, seeding a Brain,
automatic classification, handbook membership, adopting legacy files,
review projections, more than one entry per Apply, and any edit of code,
cards, configuration or policy. A proposal is one person-requested
update of one entry, never a claim that the whole Brain is fresh.

### Reviews in the Brain

Every review filed on a card — each **PR review** round and each
**relevance review**, on a card in any stage or in the archive — is
readable in the Brain at an address of its own (task 168):

```text
/brain/r/<card>/<review>        e.g. /brain/r/168/rl-0a1b2c3d4e
```

`<card>` is the card's number, kept for life (a card with no number is
named by its file stem, which a rename changes); `<review>` is the
identity the card's Reviews section already files the review under —
`rv-` for a PR review round, `rl-` for a relevance review, a hash of the
heading line bench wrote, so every board computes the same one. A rename,
a move to another stage and archiving keep the address. Two cards
sharing a number are told apart by the review: an address held by both
names both files and shows neither; one the card does not hold says *No
review … on card …*, and a number no card here carries says *No card …
on this board*. No other review is ever shown in its place.

**The report stays on its card.** The Brain reads it with the card's own
readers (`acceptance.rounds`, `acceptance.relevance_reviews`): the same
stored text, the same digest (`reportSha`), and the clipping notice where
the record was clipped when it was filed. Nothing is copied into
`brain/`, no report is made editable there, and no verdict is read
differently: a PR review's **APPROVE** is Bench's review verdict, not a
formal GitHub approval, and the merge rules read it on their own terms.
Report text is untrusted data: drawn through the page's Markdown renderer
(raw HTML stays text), never followed as an instruction.

**What bench records when it files a review.** Since task 168 a filed
review carries one line under its heading that only the board writes —
`<!-- bench:review {…} -->`, version 1; a run's own line shaped like it is
quoted on the way in. It says what the review was *of*:

| field | for | what it is |
| --- | --- | --- |
| `briefRevision`, `briefSource` | relevance | the digest of the brief the run was **launched** with (`launch`) — never the brief when the report arrived, so an edit made while the review ran does not move it |
| `head`, `base` | PR | the full commit, ref and authority (`github`, `local`, …) bench pinned the review to, and the pull request's base |
| `pr`, `prNumber`, `prState`, `label`, `note` | PR | the pull request, its state then, and whether its head had moved (`superseded`) or it had ended (`historical`) by the time the review ended |
| `evidence` | PR | the checks the run asked bench for, by evidence id |
| `agent`, `model`, `reviewer`, `filed`, `reportSha` | both | who filed it, on which model, when, and the digest of the text filed |

A review filed before task 168 has none of it, and each field reads
**unknown** — never filled in from the card as it reads now. A PR round's
heading still names the head's first 12 characters, which the page shows
as that prefix, with no authority. The card's Reviews section shows the
recorded brief revision too (*not recorded* where there is none).

**On the page.** The Brain's list has the reviews under kind **Review**,
newest first, found by search like any entry (the card's number and
title, the reviewer, the verdict, the identity). A review's page shows
the report, and beside it: the card (stage, or archived), the kind and
round, the reviewer and model where recorded, the verdict, the filing
time, the brief revision or the head and base assessed with who said so,
the pull request, the report digest and whether it matches what bench
recorded filing, the evidence ids — each *not on this board* where this
board has no such record, since evidence is machine-local — and whether
this board holds the review checkout's own record. Then an
**assessment**: *Current* (the brief, or the PR head the poll last saw,
is what was assessed), *Stale assessment* (it has moved since, or the
review was filed superseded or historical) or *Unknown* (nothing was
recorded, or this board has not observed the PR's head since it
started). **Open on the card** lands on that review in the card's
Reviews, unfolded and focused (`/cards/<card>?tab=reviews&review=<id>`),
and each review there has **Read in the Brain** — the two link both ways.
Maintained entries that cite the review (`**Source:** #168/rl-…`, a
typed source) or its card (`#168`) are listed under **Cited by**; a
`#<card>/<review>` source on an entry links to the review's page.

**Classification is a separate record.** A review nobody has classified
reads **Unclassified**, and nothing is written to say so: the list is
rebuilt from the cards and the records on every reading, so a rebuild can
lose no reason. **Classify** and **Set override** / **Clear override**
write one small file per review under `brain/reviews/`:

```markdown
# Review classification: #168 rl-0a1b2c3d4e

**Brain review:** 1
**Review of:** #168/rl-0a1b2c3d4e
**Classification:** Current
**Override:** manual · ronald · 2026-11-22T10:00:00Z — reason

## Classification history

- 2026-11-22T10:00:00Z · Current · ronald — reason
```

The file is named `c<card>-<review>.md`, keyed by the card's number and
the review's identity; it has no body, because the report is on the card.
Writes go through the Brain's one writer with everything a maintained
entry's write has: the record's exact revision (or its absence) as
`expected`, a request identity journalled before the file is touched,
this board's identity as the actor, a scoped work-state commit and sync,
and the reason rules (one line, no markup). Superseded names the
maintained entry that replaces the review's conclusions, or an explicit
invalidation. A write naming a report field (`body`, `report`, `title`, a
file) is refused, as is a review not on this board, a card with no number
(it has no stable key) and a `brain/reviews/` that is a link. A
maintained entry's create, edit, classify or retire never reaches
`brain/reviews/` or a card. A record whose review is no longer here is
kept and listed as a record whose source is missing; one that does not
follow the format is listed with what is wrong. No agent classifies a
review.

| Route | Does |
| --- | --- |
| `GET /api/brain/reviews` | every filed review with its classification, the records whose source is missing, and the records that could not be read |
| `GET /api/brain/review?card=&id=` | one review: `200`, `404` (`no-card` or `no-review`), `409` (`ambiguous`, naming the files) or `400` (not an address) |
| `POST /api/brain/review/classify` | `{request, card, review, expected, classification, reason, supersededBy? \| invalidation?}` |
| `POST /api/brain/review/override` | `{request, card, review, expected, action: set \| clear, reason}` |

The reads list the stage and archive directories, read the card files and
`brain/reviews/`, and ask the PR poll's memory what head it last saw:
they write nothing, launch nothing, run no check and fetch nothing. The
POSTs sit behind the browser-mutation door.

**Not yet:** no live migration adds provenance to reviews filed before
this change. Recovering a report whose filing failed, and the review
index, are the next section's.

### Filing a report, and recovering one that was not filed

A run's closing report — a work report, a PR update, a PR review round, a
relevance review — is filed on its card by a durable **filing
operation** (task 169, `reportfiling.py`), not by a single append.

**What is recorded, before the card is touched.** One record per report
under `manager/local/state/reports/filing/` — machine-local, never
committed: the board the run was launched from and the card's file, the
run, the **heading allocated once** (`## Relevance review — 2026-11-22
10:04 (Hazel)`; a review's `rv-`/`rl-` identity is a hash of that line),
and the exact block to append with its digests. The block is the bounded,
defused report the card would hold — never the run's log, transcript or
pinned inputs — and it is dropped from the record once the card holds it.
A report clipped when it was made keeps its clipping notice.

**Then it is filed, idempotently.** The card is read first. Its heading
line followed by this exact block means the report is already there (a
reply lost, a receipt the board never wrote): nothing is written. The
heading with other text is a **conflict**: refused, never overwritten. A
retry never allocates a new heading, so it can never make a second review
round of one report.

**Only the original card on the original board.** A retry appends to the
same file on the board the run was launched from, wherever on that board
it is now — a stage or the archive. Refused by name, with nothing written
anywhere: a board this process no longer serves (`board-changed`), a card
that is gone (`card-missing` — bench does not recreate it, and does not
guess at a card renamed since), the file in two places (`ambiguous`), a
link out of the board (`link-refused`).

**Four facts, read apart, each with its next step:**

| state | what it means | next |
| --- | --- | --- |
| **not filed** (`pending`, `unfiled` with its code, `conflict`) | the card does not hold this report; it is retained on this board | **Retry filing**, or fix what the code names; a conflict is settled by hand, then **Close** with a reason |
| **filed, commit failed or unknown** | the card holds it; `board:` commit failed, or the board stopped before recording it | **Retry commit** (commits only if the file is not already committed) |
| **filed, push failed** | committed here; sync has not published it | **Retry push** |
| **filed, not indexed** | the card holds it; this board's review index does not list it | **Rebuild index** — the review is listed in the Brain meanwhile |
| **source missing** | the index listed a review no card here holds now | restore the card or report; the index keeps it, marked |

**Retry is a person's, and bounded.** `POST /api/reports/filing/retry`
(`{operation, action: file | commit | push | resolve, reason?}`, behind
the browser-mutation door, armed on the page) is idempotent: a double
click or a resent request after a lost reply files one report. At most
20 filing attempts per report; at most 400 operations are kept, the
oldest settled ones going first and one still waiting on a person never.
At start-up the board **reconciles** every operation an earlier process
left pending: one the card holds is recorded filed (its commit *unknown*
until a person retries it); one it does not is recorded unfiled,
`interrupted`. It appends, commits and indexes nothing.

**The review index.** `manager/local/state/brain/review-index.json`
lists the reviews this board filed or indexed. `GET /api/brain/reviews`
lists every review the cards hold *whatever* the index says — a missing
or stale index hides nothing — and adds `filing`: the index's state and
revision, how many reviews are not indexed, the indexed reviews whose
source has gone, and the reports that need a person. It writes no index,
operation or card. **Rebuild index** (`POST /api/brain/reviews/rebuild`,
`{expected}` — the index revision the page read, `none` for no index;
another is `409`) is the one writer besides filing: it lists every review
on the cards once, keeps a lost source marked rather than dropping it,
records filing operations the cards turn out to hold, and touches no card
and no classification record under `brain/reviews/`.

**On the page.** The Brain's list has a **Report filing and the review
index** fold — open whenever something needs a person — with each
report's state in words, its next step and its armed action, the reviews
not indexed (each row also wears *not indexed*) and the lost sources,
and **Rebuild index**. A card's **Activity** (and its **Reviews**, for a
review) leads with the reports of its own that need a person, with
**Retry filing** or **Retry commit**.

**What it does not promise.** The retained report and the index are this
board's. A board that synced the card has neither: its Brain lists the
reviews the cards hold and says they are not indexed *here*, and
`GET /api/reports/filing?operation=` answers `404 not-on-this-board`
rather than claiming a retry. Losing `manager/local/state/` loses the
retained reports that were never filed; the run's log under
`state/agent/logs/` is still the last copy. Nothing here changes a
verdict, a gate or a classification, launches a run or fetches anything.

| Route | Does |
| --- | --- |
| `GET /api/reports/filing[?card=&operation=]` | this board's filing operations, or one; a read |
| `POST /api/reports/filing/retry` | a person's retry of one operation |
| `POST /api/brain/reviews/rebuild` | the explicit rebuild of the review index |

## Addresses: a link to every view and card

Every place the page can show has an address (task 134). Open a card,
switch to its Reviews, choose a session or a Settings section, and the
address bar follows; open that address in a fresh tab, refresh it, or
send it to a teammate on the same board, and the same destination opens
once the board has been read.

| Address | What it opens |
| --- | --- |
| `/board` | the Board. `/` is the same page, and the bar is corrected to `/board` |
| `/brain`, `/brain/e/<id>` | the Brain's list, and one entry by its stable id (task 157) |
| `/brain/r/<card>/<review>` | one review filed on a card, by the card's number (its stem if it has none) and the review's `rv-`/`rl-` identity (task 168) |
| `/cards/<card>` | the card's detail view, on its Overview, over the Board |
| `/cards/<card>?tab=<section>` | one of its sections: `brief`, `inputs`, `acceptance`, `reviews`, `activity` |
| `/cards/<card>?tab=reviews&review=<review>` | that review where it lives on the card, unfolded and focused; the bar is corrected to `?tab=reviews` (task 168) |
| `/phases`, `/phases/<card>` | Board › Phases, and one phase drilled into: its lane alone, under its breadcrumb (task 159) |
| `/sessions`, `/sessions/<session>` | the whole board's Sessions, and one session |
| `/focus`, `/focus/<session>` | Focus, and one session |
| `/sessions[/<session>]?card=<card>`, `/focus[/<session>]?card=<card>` | the same, in the activity context of one card, or of one phase and its cards (task 159) |
| `/settings`, `/settings/<section>` | Settings → Overview, or one section: `setup`, `verification` (Checks), `acceptance` (Merge rules), `agents`, `access`, `diagnostics`, `installation`, `policy` |
| `/orchestrator`, `/orchestrator/<section>` | the Orchestrator's Overview, or one section: `ask`, `trace`, `work`, `decisions`, `phases`, `recovery`, `activity`, `agreement` |
| `/orchestrator/trace?entry=<id>` | one decision-trace entry, opened, by its stable id (task 184); `card=` narrows it to that card's decisions |
| `/orchestrator/<section>?card=<card>` | that section with one card or phase selected in it, named as a card's address names it |
| `/#setup` | kept from the terminal handoff: Settings → Setup guide, corrected to `/settings/setup` |

**What names what.** A card is named by its **number** — allocated in
creation order and kept for life — so its address survives a move to
another stage and a title edit: `/cards/134`, with `0134` read as the
same number. A card with no number, or whose number another card on the
board also carries, is named by its file's stem instead (the file name
without `.md`, say `/cards/134-add-shareable-urls`), which is also how
either of two cards sharing a number is reached. Never its lane, its
title or its place in a column. A phase is a card, so it is named the
same way. A session is named by its id — the one Sessions shows the first
eight characters of. An identifier is one path segment of letters,
digits, `.`, `_`, `~` and `-`; anything else is not an address.

**Links that behave like links.** The view switcher, the Settings
sidebar, a session in the Sessions list, a phase's number on its lane and
a card's number on its face and in its detail view are real links: a
middle-click, a modifier-click or **Open in new tab** opens the
destination in a new tab, and **Copy link address** copies it. A plain
click is the page's, as it always was. **Copy link** buttons sit in a
card's detail view, on each phase's lane, in a session's header in
Sessions and in Focus, and in the head of every Settings section; each
copies the destination's canonical address on the origin this page was
served from — never a hostname written into the page — and says so, or
says the browser refused the clipboard and shows the link to copy by
hand. An address carries a path and, for a card, a section: never a
draft, a comment, a token or a credential.

**History.** Opening and closing a card, switching its sections,
changing view and choosing a session, a phase or a Settings section each
add one entry; Back and Forward walk them, restoring the view and the
selection, and add none of their own. One gesture is one entry however
many steps it takes (opening a phase card from the header chip switches
to the Board and opens the card: one entry). An address the bar already
shows is never written again, so a live reading of the board, a stream
frame, a reconnect and the periodic reading neither move the address nor
add to history. A card opened over the Phases view returns over the
Phases view on Back; opened fresh, its background is the Board. Leaving
the two card views closes the detail view, and everything unsent in it —
a comment, a note — is kept as it is on any close. A Settings draft is
kept across every history step exactly as the sidebar keeps it: the
section you go to says there is one, and nothing is applied or discarded.

**When the destination is not there, the page says so.** It does not
pick something else and does not wait for ever:

- a card the board does not list is asked of the board once (`GET
  /api/card/locate`, a read of the task files). One on the board after
  all opens; an **archived** card opens read-only, its text and nothing
  else — no tools, no actions, no tabs — with how it comes back; two
  cards sharing a number are listed, each by its own address; a number
  or name no card has reads **No card #… on this board**;
- a section a card does not have is said in a toast, and the card opens
  on its Overview at its canonical address;
- a session this board does not have — its history removed, or recorded
  on another board — reads **not on this board** in Sessions and Focus,
  with nothing selected in its place. An ended session whose history is
  kept stays linkable and says **ended**;
- a phase the board does not have, a card that is not a phase, and a
  Settings section that does not exist each say so above or in place of
  the content.

**Opening an address only reads.** Direct entry and refresh serve the
same page `/` does, for exactly the shapes above, with the page's own
policy; what is shown is read through the GETs the views already make.
No agent is launched, no check is run, no card moves, no setting is
applied, no armed action is replayed and nothing is fetched from a
remote. Every other path keeps its answer: an unknown path, a malformed
identifier, a second path segment, a missing `/api/…` endpoint and an
unlisted asset are `404`, and the Host check applies first, as it does
to every request. A link grants nothing: whoever opens it reaches the
board only if they could already reach the board.

**Limits.** Addresses name destinations, not moments: a menu, a sheet, a
fold, a scroll position and a half-typed field are not part of one. An
address is about the board that served it; another board resolves the
same number against its own cards. The Board, Phases, Sessions and Focus
with nothing chosen have no **Copy link** button of their own — their
links in the view switcher are copied the browser's way.

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
host is decided rather than left to omission: **▸ start work** refuses
it (a phase's work belongs to its members' cards); **↻ act on PR** is
allowed on a phase card in `review/` and repairs the phase's own
integration branch and PR, never a member (task 174, see "Repairing a
phase's integration PR"); and the read-only pair — **◔ still true?** and
**◔ review PR**, the latter told the phase's own branch — are allowed. The
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

A report is filed on the card the run was launched for, on the board it
was launched from, in the stage the card is in when the run ends — or not
at all (task 148). The run's board is bound at launch, and a report is
never written into a board this process no longer serves. Nor does it
recreate a card that has left that board: appending opens an existing
file and never creates one, so a report can no longer become a
report-only phantom card. Each refusal is narrated in the ticker, and the
run's log still holds the report.

A report is the run's words, and the card's history is also where the
board keeps records of its own — acceptance decisions, finding events,
discussion entries, each a line starting `<!-- bench:`. So a report line
shaped like one is quoted (`> `) on the way in, by the same helper, in
the task file and the PR body alike: still there to read, never parsed
as a decision a person made.

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

### Time on this card: cycle time without a second collector

A run's progress says what it is doing. It does not say where a ticket's
ninety minutes went — how long it queued, how long agents ran, how much of
that a check was in flight, how long it waited for review or for its
phase. The card Overview's **Time on this card** answers that (task 165),
and `GET /api/card/timing?file=&stage=` returns the same reading
(`cycletime.py`). It is computed from records bench already keeps, and
nothing new is collected:

| measured from | what it gives |
| --- | --- |
| the card's stage moves (`movements.history`: committed `board:` moves and the move journal) | the stage timeline, every visit, and the lead time |
| each run's progress record (`progress`, task 73) | when a work, act-on-PR or review run opened and the ending the board observed |
| bench's evidence records | each check's request, start and end, its preparation steps, its attempt number, and requests answered by reuse |
| the phase card's log (`<n> merged into …`) and the delivered record | a phase member's merge into the phase branch, and its phase's delivery |
| the run registry and its session | what is running now and its last tool event |

**The categories overlap, and none is a part of a total.** A check runs
inside a run, which runs while the card sits in In progress. So each row
is a separate measurement over the same clock, and the only whole is the
**lead time**: from the card's first recorded move (its creation, where
that is on record) to its newest arrival in Done, or to now while it is
open. Adding the rows would count the same minutes several times, and
nothing on the page or in the reply does it.

| row | what it measures |
| --- | --- |
| Lead time | first record → Done; the ticket, not a run |
| Queued | Backlog and To do, every visit summed |
| In progress, In review (stage) | time the card sat in that stage, worked or waiting |
| Implementation, Review, Repair runs | the union of that kind of run's spans — a run, not the ticket |
| Verification | the **union** of every check's window (request to ending), so checks running side by side count their shared minutes once; the plain sum across checks is shown beside it, with how much of it overlapped, the time inside check processes and the time preparing |
| Agent time outside checks | implementation and repair runs' union, less the time a check was in flight. **Not measured coding time**: bench sees a run's span, not what the agent did inside it |
| Waiting for integration, Waiting for delivery to main | a phase member only: from its last arrival in Review to its merge into the phase branch, and from there to its phase's delivery. In the phase branch is not delivered |

**Per-run time is not ticket time.** Each run is listed with its own
span, its checks and its time outside them; a ticket that was queued for
a day and ran for an hour has a lead time of a day.

**What is in flight** is listed with its elapsed time: a check by name,
attempt and phase (preparing, or running the check), with the evidence
sheet as its next step; a run with its **last progress report**
(agent-reported, not verified) beside its **last tool activity** (observed
by bench — activity, not progress) and its process, with **‖ hold** as
the person's action. No percentage is computed and nothing is inferred
from either.

**Unknown is said, never filled.** Each of these is listed in its words,
and its time is in no total:

- a run whose ending was not observed (the board restarted while it ran)
  and a check its record says the board stopped during;
- a move made by hand with no record (the card is not where its last
  recorded move put it), a card whose creation is older than the history
  bench reads, or a work state in no repository;
- a phase member that arrived in Review inside the minute the phase log
  dates its merge: the merge could be either side of that arrival, so
  its wait for integration (under a minute) is unknown;
- **clock and order anomalies**: a move, run, check, merge into the
  phase branch or phase delivery dated after the reading, ending before
  it started, or committed out of order (a replayed or rebased board
  commit carries the replay's date); a merge the phase log dates before
  the card ever reached Review; a delivery dated before the merge. That
  interval is given no duration rather than a negative or invented one.

A row with nothing recorded reads *none recorded*, never `0s`. A phase
log line is written to the minute in this board's local time, truncated,
and its precision is said with it: the wait for integration is measured
from the last arrival in Review at or before the logged minute.

The route answers about the card where it is: naming a `stage` the card
has left is `409` with `moved` and the stage it is in now, and the page,
which asks again whenever the card's stage changes, reads that instead.

**Provenance.** Every run is named by its agent id and every check by its
evidence id, attempt, commit and who asked, each linking to the ◫
evidence sheet. Every input is machine-local, so the reading is this
board's: a replica that synced the card has its moves and none of its
runs or checks, and shows exactly that.

**Reading is free.** Opening the card, the Overview and the route read
files, the registries and the memoised move history; they launch,
fetch, cancel and write nothing, and they grant no authority — there is
no auto-cancellation and no new orchestration step behind any of it. The
fixtures in `tests/test_cycletime.py` hold every row to exact totals
(nested and overlapping checks, retries, concurrent runs, a reopened
card, a phase member, missing data, anomalies and running intervals),
and `verify/browser_cases/case_cycle_time.py` holds the drawn values to
the reply at 1440 and 390 pixels in both themes.

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

Four structured inputs, in this order, and nothing else:

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
3. **The orchestrator's work-base decision** — only where neither of the
   above says anything: a `stacked` decision recorded on the card starts
   the work from a Review prerequisite's pinned commit and targets its
   branch (see "Choosing a work base: main, or committed work in
   Review"). A card with no such record is unaffected.
4. **The application default** — `BOARD_APP_BASE`, `main` unless the
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

### Stacked cards

A card whose `**Base branch:**` names another card's work branch —
`task/<stem>` for an ordinary card, `phase/<stem>` for a phase card — is
**stacked** on that card, its **parent**. No header is added and nothing
is stored twice: the graph is derived once per read
(`taskfiles.weave_stacks`), beside phase membership, and runs one way, so
it cannot disagree with itself. A parent may carry several children; a
child has one parent. A loop of bases has no bottom to start from, so it
is `plan drift` and the launch refuses. A base no card owns is an
**external** base, a card-shaped name with no card is **missing**, and a
name with two readings is **ambiguous** — each said, none guessed. A
phase member's relation to its phase stays **integration**, with the
phase's own semantics.

`/api/state` carries `stacks` (task 117): per card with something to say,
its parent, the cards stacked on it, its path from the bottom of the
stack, its place on that path, and three targets named apart — the
**selected base** (the branch and commit the work was cut from), the
**review target** (what its change request targets) and the **landing
target** (where it must end up for `done/`: the application base, after
its parent lands). Each edge has a state in the server's words:

| state | what it means | next, and who |
| --- | --- | --- |
| `on-parent` | the child contains its parent's tip | nothing |
| `parent-moved` | the parent has commits the child does not contain | you: **Update from <parent>** (additive) — bench will not rebase |
| `parent-rewritten` | the parent no longer contains the commit the child was cut from | you: settle it by hand |
| `parent-landed` | the parent was delivered; the child may still target its branch | you: **Retarget onto <target>** |
| `parent-landed-differently` | the host says merged, and the parent's tip is not in its target (a squash or rebase merge) | you: bring the target in first, then retarget |
| `retargeted` | the parent landed and the child now targets where it landed | nothing |
| `parent-closed` / `parent-gone` | closed unmerged, or the branch is gone with no delivery record | you: decide the base, then ⌖ replan |
| `parent-retargeted` | the parent's change request targets another branch than its plan | you: settle the parent's target first |
| `target-changed-on-host` | the child's own change request targets another branch than its plan | you: ⌖ replan, or change it back |
| `parent-not-started`, `not-started` | no work yet on one side | — |
| `external-base`, `parent-missing`, `parent-ambiguous`, `cycle`, `integration` | the mapping itself, as above | fix the header where it says so |

**A position is about a path.** Along a chain (every card in it carries
at most one child) a card reads `2/3`; once the stack branches there is
no single order, so a card reads `step 2` — its depth on its own path —
and no count of the whole is invented. The ⌖ chip says it (`⌖ on #56 ·
2/3`, `⌖ under #57 · 1/2`), and the Overview's **Stack** block lists the
path with each card's change request and state, the cards stacked on
this one, and the three targets. The page computes none of it.

**Reading is free.** The reading uses this checkout's own refs and
remote-tracking refs (the last fetch, never a new one), the recorded
plans, the PR snapshots the poll persisted and the delivered records —
no fetch, no hosting-provider call, no write, no check and no worktree.
**Check the remote** is the explicit action that refreshes the refs it
reads. Nothing here rebases, force-pushes, retargets or merges a child
into its parent; a stacked card whose own requirements are met (or not
configured) reads `waiting-for-parent` until its parent has landed.

The recorded plan is version 2: it adds `stack` (the parent card, the
branch and that branch's tip when the work was cut) and `landing`, and
changes nothing else. A version-1 record is read unchanged and its stack
reading is derived from the card; a reader that predates version 2
ignores the new keys.

**Two safety rules hold the stack's branches.**

- **Opening a child publishes nothing of its parent.** A change request
  whose base is another card's branch is opened only when that branch is
  on the remote, and only when the child carries none of the parent's
  commits the remote lacks; otherwise it is refused, naming the parent's
  own publication as the remedy. A phase member's PR keeps publishing its
  phase branch as before, and a base no card owns is unchanged.
- **Cleanup keeps a branch something targets.** Merge & clean up deletes
  a branch on the remote only when no card on the board still targets it
  and the hosting provider says no open change request does
  (`changes_targeting`). An open change, a dependant card, or a provider
  that cannot answer keeps the branch, and the ticker says which.

### Maintaining a stack: three confirmed actions

The reading offers, and a person acts (task 119). A stacked card's
**Stack** block carries a button for each action its edge makes worth a
look — and only on an ordinary card stacked on a card: a phase member's
relation to its phase is the phase runner's, and a phase card lands as a
whole. A button opens the action's **sheet** (`POST
/api/card/stack/preview`): the identities it is bound to — this card's
head, the parent's tip, what it targets now and would target, the
target's tip, the change request — what it does, what stops it and, for
a retarget bench may not make, the exact manual step. Opening it changes
nothing. Confirming is a second, armed click (`POST
/api/card/stack/action` with the identities the sheet showed); the server
reads everything again and refuses, naming what moved, when any of it
differs. Every action is refused while an agent works on the card, while
it is being merged and cleaned up, and while its worktree is dirty, on
another branch or not this repository's. Nothing calls an action but the
click: no beat, no move and no read.

- **Retarget onto <target>** — once the parent has landed, the card's
  change request targets where it landed. Where there is a change request
  and the provider's `retarget` reads `available` at that moment (the
  installed CLI's own `--help`, read locally), bench changes the base on
  the host and reads the change back; the recorded plan and the card's
  `**PR base:**` follow what was read back, never what was hoped. The
  edit is **not** conditional on the head (STACKING.md, C8): bench reads
  the head and base immediately before and after, refuses drift that was
  already there rather than "correcting" someone else's edit, and a host
  that did not take the edit, or could not be read back, records nothing
  and says so — nothing is undone on the host. `unavailable` or
  `unknown` makes **no** provider mutation call at all: the sheet names
  the manual step (on GitHub, Edit → base, or `gh pr edit <n> --base
  <target>` by hand), and once the host is seen targeting the new branch
  the same action *adopts* it, with no edit. A card with no change
  request yet changes its plan alone. A parent that landed by squash or
  rebase needs its target brought into this branch first; bench will not
  rebase.
- **Update from <parent>** — an additive merge of the parent's tip into
  the card's own worktree (recreated from its branch when it was cleaned
  up). A conflict is aborted, the conflicting files named, and the branch
  left exactly as it was. The new head is published with a plain push
  only when the parent's tip is already on the remote, so a child never
  publishes its parent's work for it. Nothing is reset or force-pushed.
- **Finish: clean up** — for a card whose head the application base on
  the remote already holds, read off the remote itself rather than the
  last fetch: the workspace is cleared under merge & clean up's rules
  (uncommitted work keeps the worktree and its branch, a branch something
  still targets is kept on the remote), the card moves to `done/` and a
  delivered record is written. Nothing is merged. A child with no commits
  of its own — its head still the commit its work was cut from, or one
  that commit already contained — is not offered it: there is no work of
  its own to have delivered. That is read from the recorded cut, never
  from where the parent is now, so a child whose work landed through its
  parent's branch is still finished.

**A retarget is a new review context.** It changes the change's diff and
integration even when the head did not move, so it is recorded on the
plan (`retargets`: from, to, the head, when, how) and the merge rules
stop counting a Bench verdict or a formal approval given before it
(`verdict-before-retarget`, `approval-before-retarget`, both *needed*).
A **⌖ replan** that changes the target records one too, and the history
is carried whenever the plan is recorded again — **▸ start work** on an
existing branch and **↻ act on PR** re-record it — so a later launch at
the same head cannot make an earlier approval count again. Check evidence
keeps its own identity rules: a pass whose declared identity still holds
is still a pass. An update from the parent is a new head, and everything
about the old head is history under the existing rule.

Nothing here restacks, rebases, force-pushes or merges a child into its
parent, and nothing relies on what a host does by itself when a parent
merges or its branch is deleted: those cells of the evidence matrix are
still *not observed*, and the reading reports whatever the host did
(`target-changed-on-host`, `parent-closed`) rather than assuming it.

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

**Approved means actually readable.** A headless run has nobody to answer
a permission prompt, so a file the vendor will not let it read is not
approved in any sense that matters. The pinned copies (`AGENT_CONTEXT`)
and, in an external layout, the workflow briefs the prompt names outside
the checkout (`AGENT_GUIDANCE`, by canonical path and approved hash) are
each rendered by the claude adapter as one exact file read —
`Read(//<absolute path>)` — with an edit of the same path denied. Nothing
broader: no wildcard, no directory, no blanket read, no bypass. An entry
that cannot be granted exactly — malformed, not canonical, a link, not a
regular file, or no longer the approved content — refuses the launch
before the vendor starts rather than being dropped or widened, so a brief
edited after the pin fails closed. The source a selection was copied from
is never granted because the selection names it. That is generated
permission text, and the doctor's `context-reads` row says so; whether the
installed CLI honours it is the opt-in, billable
`develop/claude-access-smoke.py` (see `core/adapters/README.md`).

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

## Required information: inputs and tools

Approved context is the operator's: files every run of an intent is
given. It says nothing about the screenshot, the design document or the
API specification one particular card links to — and an agent that
starts without one of those builds something else, slowly, on your
budget. So each card has its own **required information**, checked
before implementation starts (task 132).

### An example, end to end

A card says: match the login screenshot
(`[screenshot](../../reference/login.png)`), follow the API specification
(`[API](../../reference/api.md)`), and the design is in a document on the
team's wiki. Its Inputs tab lists all three. The
screenshot and the specification are files in this board's `reference/`,
readable, and of kinds the configured harness reads as content (an image
and Markdown): **Available**. The wiki is not a host the operator listed,
so bench has no approved way to retrieve it: **Blocked**, with the fix —
*save an export under `reference/` and link that*. The card reads **Needs
information**.

Moving it into To Do is allowed — planning is never held — and starts a
bounded check. Moving it into In Progress, or **▸ start work**, is
refused with the same sentence, and the refusal opens the Inputs tab.
Someone exports the design as a PDF into `reference/`, edits the link,
and the tab reads **Ready**. Had the wiki been a listed host returning a
sign-in form with HTTP 200, that would still be Blocked: a status code is
not the document.

### The section: `## Inputs and required tools`

The contract is written in the brief, where a person reads it, edits it
and a refinement can change it — not in a second store:

```markdown
## Inputs and required tools

- input, required: ../../reference/api.md — the endpoints to follow
- input, required, follow: ../../plans/design.md — and the documents it links
- input, optional: ../../reference/old-screen.png — for comparison
- input, non-input: https://example.com/background — context for people
- tool, required: image-reading
- tool, required: check unit
```

Each line is `- input|tool, <classification>[, follow]: <source> — note`.
An input is **required**, **optional** or **non-input**; a tool is
required or optional. A source is a path relative to the card (as a
Markdown link would be), `work:`, `tool:` or `app:` plus a relative path,
or an http(s) address. `follow` asks bench to read the references inside
that document too, two levels deep at most. `- None.` says there is
nothing to name. The seed template carries the section; a work root's
own template is never rewritten.

Tools bench can verify against the run that would start, and nothing
else: `check <name>` (a named check that run may ask for), `command
<prefix>` (one of its command grants), `capability <word>` (declared by
its harness), `image-reading` and `pdf-reading`. A required tool the run
is not granted reads Blocked and names the configuration that would
grant it. **Nothing is granted, installed or widened because a card
asks.**

**Discovery proposes; a person classifies.** Every Markdown link, image
and bare address in the brief's prose is found — never one inside a code
span or fence, and never one in the card's history (reports, reviews,
the discussion). A reference the section does not list is shown as
proposed and counts as **required** until someone classifies it; the
Inputs tab's **Classify** writes that line into the section, bound to the
brief the person saw (a brief that changed meanwhile is refused with
nothing written). Discovery does not prove a card names everything the
work needs, and the tab says so.

### Four facts, kept apart

| fact | means |
| --- | --- |
| discovered | written in the brief, or in a document selected with `follow` |
| retrieved | bench read the content itself: a file inside one of this board's roots, or a remote document from a host in `BOARD_INPUT_FETCH_HOSTS`, without a credential |
| approved | the intended run's harness declares a delivery route (`inputs.delivery` in its `capabilities.json`: `exact-read`, a pinned copy granted by one exact read rule) and reads that kind of content (`text`, `image`, `pdf`) |
| delivered | what a launch pinned and granted, with its hash, in the run's manifest (`information.received`) |

An operator-readable file the harness cannot be given is not Available.
The claude adapter declares `exact-read` and text, images and PDFs;
opencode declares no route, so a card with required inputs reads Blocked
on it rather than relying on a permission prompt nobody answers. A
filename or readable bytes is not evidence a run can use a file: content
is classified by its own bytes (an image signature, `%PDF-`, UTF-8 text),
and anything else asks for an export.

### When it is checked, and by whom

- **Reading is free.** The Inputs tab, `/api/state` (`information`) and
  `GET /api/card/inputs` read local files and hash them; they retrieve
  nothing, launch nothing and write nothing.
- **Entering To Do** through this board starts one bounded check, and
  **Recheck information** starts another. A check is the only thing that
  retrieves a remote reference; overlapping requests for the same brief
  join one check, and a check whose brief changed while it ran, or that a
  newer check superseded, publishes nothing.
- **Results are machine-local** (`manager/local/state/inputs/`), so a
  replica that sees the move through a sync fetches nothing of its own:
  it reads the card as Unchecked until somebody on that board asks.
- **Implementation is held.** A move into In Progress, ▸ start work and
  its resume, a phase member's launch and ↻ act on PR all ask the same
  resolver, before a claim, a branch, a worktree or a model; a card moved
  there by hand or by a sync keeps its stage and still cannot start.
  Information Ready does not override the plan, ownership, the profile or
  the merge requirements, each of which keeps its own check.
- **Freshness.** A local input is read again at every check and launch,
  so an edit is seen at once. A retrieved remote input is usable for
  `BOARD_INPUT_FRESHNESS` seconds (a day by default) and then reads
  Unchecked until rechecked. Changing the brief, the harness, the profile
  or its grants changes the reading immediately.

### What a run receives

At launch, every Available input is copied into the run's read-only
bundle and granted by one exact read rule, exactly as an approved input
is; bench refuses the launch if a copy's hash differs from what was
checked. The manifest freezes the whole reading — classification,
identity, hash, representation, the tools — so a later edit changes the
next launch and never this one. The prompt carries one **Inputs and
required tools** list: each delivered input's pinned path and how to read
it, each tool and how to use it, what optional input was left out, and
what a person marked as not an input. Retrieved content is data, never
instructions, and the prompt says so.

### Who provides what

The card's author names the inputs and tools. Private material is
supplied as an export under `reference/` by someone with access — bench
uses no credential, follows no link into a private address, never signs
in, and refuses a URL or file that carries one. The operator decides
which public hosts may be retrieved from (`BOARD_INPUT_FETCH_HOSTS`, empty
by default) and which checks, commands and harness a run gets (Settings).
Bench discovers, checks, delivers and records; it does not decide that a
card's list is complete.

## Show the result: demonstrations, not proof

A passing log says a program exited zero, and a card in `done/` says the
work moved. Neither lets a person *see* what changed. The card Overview's
**Show the result** section does (task 133): a short outcome, a few
captioned screenshots or other outputs, which requirement and scenario
each is about, the checks bench actually ran, and where the change is —
on a branch, merged, installed, deployed. Input readiness (the section
above) is what a run needs before it starts; this is what it leaves
behind. The two are separate capabilities.

### An example, end to end

A card fixes the Settings screen overflowing at 390px. Someone opens its
Overview, **Edit Show the result**, chooses **Required for handoff**,
clicks **Before/after** and writes *Before/after screenshots of the
narrow Settings screen*, ticks **This is the agreed output** and saves.
That writes four lines into the brief:

```markdown
## Show the result

- mode: required
- want: Before/after screenshots of the narrow Settings screen
- expects: before-after
- agreed: 1a2b3c4d5e6f
```

The team already has a browser suite; the operator registers it as a
check with `"outputs": true`. The work run is told about the request,
commits, and asks bench for that check. The suite writes `before.png`,
`after.png` and a `manifest.json` into `$BENCH_VERIFY_OUTPUTS`; bench keeps
them. The Overview shows the pair side by side at the same viewport,
captioned, naming the revision each shows, linked to the check run that
made them, with *on a branch, not merged; installed and deployed unknown*.
Had the run ended without them, the card would have stayed in
in-progress with the exact remedy, and a person's drag into review would
have been refused with the same sentence.

### Three modes, and only one holds anything

| mode | extra work | what is missing means |
| --- | --- | --- |
| **Off** (the default; no section reads as Off) | none | nothing — outputs a check kept anyway are still shown |
| **Requested** | the run is asked to capture something useful through an approved check, and to say what it could not | nothing is held; the status reads *Requested* until output exists |
| **Required for handoff** | the agreed output must exist for the current commit | the move into `review/` is refused — a person's move and a work run's own — with the remedy |

**Required needs agreement first.** An empty or subjective request
(*something convincing*) is not enforced: the status reads *Required —
agree what to show*, and saving with **This is the agreed output** is
refused until the description names a screen, a scenario or an output.
The `agreed` line is a hash of the description and its kind, so editing
either by hand withdraws the agreement. **What is checked is the kind and
the commit**: an image for Screenshots, text, structured data or a file
for Example output, a before and an after with the same pair name and
viewport for Before/after — current output of that kind from a passing
bench-run check at the card's head commit. Whether it shows what was
agreed is the reviewer's judgement, not bench's.

**Nothing else changes.** Required is not a merge gate, not a definition
of done and not a sign-off: it holds one move. Requested and Off never
acquire blocking semantics. Selecting Required grants the run no capture
capability and no broader execution. A card with no section, and every
card that existed before, is Off; `BOARD_RESULT_DEFAULT_UI=requested` is a
project's opt-in that makes the user-interface setup cards the board
itself writes start as Requested — never Required, and never applied to
an existing card. On a card that reads as visual, the Overview offers
*Would screenshots help you review this change?* — **Request screenshots**
opens the editor and saves nothing; **Not now** is remembered on this
board and writes nothing into the card.

**A run keeps what it launched with.** The request is frozen in the run's
manifest (`demonstration`) and told to it in one paragraph of its prompt;
an edit afterwards reaches the next run. Changing the setting while an
agent works says so. On a `done/` card, **Request a demonstration** first
shows what existing output could be reused and what new capture would
need — the branch checked out again and a check run against it — and
saving it neither reopens the card nor launches anything.

### Who provides what

Your team provides the demonstration programs — a browser suite that
takes screenshots, an API check that keeps its request and response, an
export check that keeps the file it generated. An agent can help write
them; its sentence that something works is not output. The operator
registers a program as a named check with `"outputs": true` (Settings →
Checks), which is the approval: only such a check is handed
`$BENCH_VERIFY_OUTPUTS`, and declaring it changes no check identity and
stales no pass. With no such check, the Overview says capture is
unsupported and how to get it; nothing is invented. Bench runs the check,
keeps its outputs, binds them to the run and presents them.

Suitable output by task type: a UI change — screenshots, before and after
at the same viewport for a comparative claim; an API change — the
sanitized request and response the check asserted on; a bug fix — the
reproduction before and after; a performance change — comparable
measurements with their conditions; an export — the generated file. An
internal refactor needs no picture: leave it Off, and the Overview's
*What ran* list and the work report are the behaviour-preservation
summary.

### What a program writes

Files go in `$BENCH_VERIFY_OUTPUTS`, two directory levels at most. An
optional `manifest.json` beside them describes each:

```json
{"v": 1, "summary": "Settings no longer overflows at 390px",
 "outputs": [{"file": "after.png", "caption": "Settings at 390px",
              "scenario": "narrow settings", "viewport": "390x844",
              "criterion": "narrow", "role": "after", "pair": "settings",
              "data": "fixture", "target": "candidate"}]}
```

`role` is `before`, `after` or `single`; `data` is `fixture`, `mock`,
`synthetic`, `local`, `staging`, `production` or `unknown`; `target` is
`candidate` (the program started this checkout) or `running-service`;
`revision` names the commit a picture shows when it is not the one the
check ran on (a before); `partial: true` says the capture is incomplete.

### How far each output can be trusted

Every output records its id, hash, kind, size, the run and check that
produced it, the card, the commit and whether the source had uncommitted
work, the capture time, and the manifest's description. Two strengths are
kept apart: what **bench recorded** — the run, its outcome and the source
it was bound to — and what **the program said** — the scenario, the
viewport, the data, and which build the picture shows. Bench does not
watch an application start, so that binding is always shown as the
program's account.

Only an output from a passing run at the current commit, on clean source,
complete, from non-mock data and of the candidate reads **Current**. Every
other reads what it is: *from an earlier commit*, *from a check that did
not pass*, *partial*, *captured with uncommitted work*, *mocked or
synthetic data*, *of an already running service*, *the program says it
shows another revision*, *the criterion it is about was reworded*, or
*expired / withheld / not on this board*. A new commit or a reworded
criterion never inherits a current claim.

The current commit is the tip of the card's branch while it exists. Once
merge & clean up has deleted the branch, it is the commit this board
recorded delivering, so a delivered card's outputs and checks keep their
reading; a branch that exists again (the work reopened) is the newer
question. An output whose program wrote no `manifest.json` entry for it
says so beside it: what it shows, its data and whether it is of this
checkout or an already running service are not stated.

A demonstration, a check result and an acceptance are three records.
Output never accepts a criterion, never turns a failed check into a pass
and never completes a card; the section shows acceptance counts beside it
and links to the Acceptance tab, and says *installed* and *deployed* are
unknown — a merge is not a deployment.

### Where outputs live, and for how long

Under `manager/local/state/artifacts/<card>/`, one record and one bytes
file per output: run-owned, machine-local, outliving the run's temporary
directory, a restart and the card's worktree, and never committed into
ticket history. They are served only through the board
(`/api/artifact`), so another device that can reach the board can open
them; a path in a log is not a deliverable. Another board that only syncs
the card shows the request and no outputs, rather than claiming bytes it
does not have.

Limits, each in `manager/local/.env`: `BOARD_ARTIFACT_MAX_BYTES` per file
(8 MiB), `BOARD_ARTIFACT_RUN_FILES` per run (24),
`BOARD_ARTIFACT_CARD_BYTES` per card (64 MiB; the oldest bytes expire
first) and `BOARD_ARTIFACT_RETENTION_DAYS` (90). Expiry removes the bytes
and keeps the record, marked expired with the reason. **Withhold** (in an
output's Provenance fold) removes a sensitive output's bytes and
description and keeps the fact that it existed, with who and why.

### Safe capture and safe viewing

Capture only through an approved check, in its execution root, with
fixture data: no production mutation, real email, payment, credential or
public upload is authorised by a demonstration, and an unavailable
browser, application or fixture is a prerequisite to report, not a
reason to broaden access. Files are classified by their bytes — PNG,
JPEG, GIF and WebP are images; UTF-8 text and JSON are shown as plain
text, scrubbed of anything credential-shaped; everything else downloads
as `application/octet-stream`. HTML, SVG, XML and scripts are downloads
only, executables, links, files outside the directory, oversized files
and credential-shaped names are refused and the refusal recorded. Every
reply is `nosniff` and sandboxed. Scrubbing text does not sanitize pixels:
an image is kept as made, and the Overview says nothing inspected it.

**Opening the result runs nothing.** The Overview, `GET /api/card/result`
and `GET /api/artifact` read files; no capture, check, checkout or agent
starts. The writes are `POST /api/card/result/setting` (bound to the
brief shown), `/dismiss`, `/preview` (reads, says what a request would
need) and `POST /api/artifact/withhold`, all behind the browser-mutation
door.

## From evidence to merge: the whole journey

The next four sections — verification, gates, delivery status and
acceptance — are reference. This one is the route through them, for a
person who wants a card merged and asks only this: **what is happening,
what do I do next, and what, if anything, prevents delivery?** The card's delivery status answers all three in one word, one
sentence and one primary action (see "Delivery status: one answer per
card"). Nothing below requires reading a policy hash, a root name, an
evidence id or a gate scope.

### Checks, review and merge requirements, in plain words

- **A check** is a program your team provides — a test suite, a build —
  that bench runs on request and records the result of, bound to the
  exact commit it ran on. An agent saying "tests pass" is not a check
  result; bench's own record is.
- **A review** is a judgement of the change at that exact commit. By
  default it is one review: **◔ review PR**, which assesses each
  acceptance criterion and says what it could not judge. Copilot, comments
  and agent summaries are shown and counted nowhere.
- **Merge requirements** are what you decided must be true before a card
  merges: which checks must have passed on the commit about to merge, and
  which review. They are listed on the card as `m of n merge requirements
  met for <commit>`, counting required obligations only — an optional
  check never inflates the count and never blocks. Registering a check
  does not make it required; requiring it is a separate decision.

Three signals stay apart and never stand in for each other: **local
verification** (bench ran the check and recorded it), **independent
review** (a review of the exact commit — bench's agent, or a separate
person on GitHub when you ask for that), and **CI** (your remote's own
runs, shown as CI and never counted as a Bench check).

### The recommended route: one required check, one review

One canonical check, run on a fresh checkout of the exact commit, that
the implementation run, the PR-fix run and the review run may all ask
for — and one review:

```jsonc
{
  "checks": [{"name": "unit", "run": "python3 -m unittest discover -s tests",
              "root": "source", "prepare": ["deps"], "timeout": 900}],
  "prepare": [{"name": "deps", "run": "python3 -m pip install -e .",
               "network": true}],
  "profiles": {"work": {"verify": ["unit"]}, "act-pr": {"verify": ["unit"]},
               "review": {"verify": ["unit"]}},
  "gates": {"acceptance": {"checks": ["unit"], "review": "bench"}}
}
```

The implementation run asks for `unit` after its last commit; the
review asks for the same check at the same commit and is handed the
existing pass, so it runs once. `root: "source"` keeps ignored state
(`node_modules`, caches) out of the result, which is what lets one pass
be reused safely.

Add anything further only for **distinct assurance**, and each addition
says what it buys:

| addition | what it buys | when |
| --- | --- | --- |
| a second required check, such as a browser suite | a different environment and coverage — never "the same check again" | the first check cannot see that class of failure |
| `"review": ["bench", "separate-reviewer"]` | an independent person's approval as well as the agent's review | independence is the point (GitHub refuses approval from the account that opened the PR) |
| `"signoff": "criteria"` | a named person accepts each criterion against the merging commit | visual, business or regulated decisions |
| `"accepts": ["unit-review"]` on `unit` | a second name the project keeps for its own reasons, counted only when bench proves the definitions equal | an existing setup you do not want to rename |
| `BOARD_VERIFY_ON_REVIEW=1` | the missing required checks are requested when you move a card into review | you would otherwise always click **Run required checks**. Off by default |

### The journey, counted

A **decision** is a click that commits you to something (an armed
action is one decision), a **look** is opening something only to
understand the state, and a **run** is a check execution. `k` is the
number of acceptance criteria. The counts are computed from explicit
journey rows (`tests/fixtures/delivery-journeys.json`), and a test holds
this table to them.

| Journey | Decisions | Required looks | Check runs |
| --- | --- | --- | --- |
| SAFER36, before | 3 + k | 4 | 1 |
| SAFER36, after (evidence with a result identity) | 2 | 0 | 0 |
| SAFER36, after (legacy evidence, no result identity) | 3 | 0 | 1 |
| Ordinary card, before (SAFER-like checks) | 5 + k | 1 | 2 |
| Ordinary card, after (recommended setup) | 5 | 0 | 1 |

Every decision left is one a person should make: committing to the
work, consenting to a launch that spends tokens, an independent review
of the exact commit, and the merge with its confirmation. An optional
**Run required checks** is added when the agent did not ask; with
`signoff` configured the `k` criterion decisions come back, as the human
assurance you chose.

**"Zero redundant reruns" holds for evidence recorded with a result
identity** — every record written since result identities exist. A pass
recorded before them (the SAFER36 card's own evidence is one) cannot
prove what it depended on, so an unrelated policy edit still reads
`stale / policy-changed` on it, exactly as it did when it was written.
It needs **one** fresh run. That run records an identity, and from then
on unrelated edits leave it standing and later asks at that commit are
answered by it. The legacy record is never rewritten or upgraded.

### Which record decides, exactly

The gate fixes the commit H it would merge first, and only then reads
records. For a requirement on check C with coverage `want`, a record r
is **relevant** when:

```
relevant(r) =
      ( r.check == C
        or ( r.check in C.accepts and equivalent(r, C) ) )
  and overlaps(r.scope, want)
  and r.source.commit == H          -- a request still preparing counts
  and r.outcome != "denied"         -- a denial ran nothing
```

Relevant records are then partitioned. **Current**: it has a result
identity and nothing in its definition (check, coverage, programs,
preparation, inputs, environment, runner) has moved. **Outdated**: it
has one and something moved. **Legacy**: it has none. Current and legacy
records decide, newest first by request time, then end time, a non-pass
winning a tie: a run in flight holds the requirement pending; a covering
pass over a clean, bound source meets it; a failure, preparation failure
or error is the problem; a cancellation or restart means it is needed
again. Outdated records decide nothing; if nothing decides, the newest
outdated record's own changed dimension is the reason. Records at other
commits are history, counted and never the reason.

**Legacy stale precedence.** A legacy pass is read with the rules it was
written under, so after a policy edit it reads `stale /
policy-changed` — and a stale pass is an *answer*: it is the reason, and
no older pass at the same commit is resurrected past it. Every legacy
record predates every current one, so one fresh run at H is newer than
all of them and decides.

### What bench binds, and what it does not

- **Declared, bound:** the command and its arguments, the root class, the
  time limit, preparation steps and their programs, the programs invoked
  (and a script's interpreter, one level), declared `inputs` by content,
  declared `env` values by keyed hash, the names of every variable passed,
  and bench's own runner revision.
- **Not bound:** libraries and modules the programs load, services they
  call, files read by an undeclared path, ignored files in a `worktree`
  root, and the values of undeclared variables. Declare `inputs` and
  `env` for what matters, pin dependencies in tracked lockfiles, and
  prefer a `source` root.
- **The HMAC key.** Declared `env` values are bound by a key that exists
  only on this machine (`state/verify/env.key`). Replacing or losing it
  reads every such pass `env-changed` — fail closed — and costs one fresh
  run of each. There is no rotation and none is needed.
- **Exact-head revalidation.** On a gated board the gate is read again
  immediately before every merge, the merge takes the commit the gate
  read, and a merge on origin passes `--match-head-commit`. A push landing in between is a
  refusal, not a stale merge. A ready status on the page is never the
  merge's licence.
- **Bounded diagnostics.** A run whose check failed gets the tail of the
  check's output in the reply — scrubbed, at most 80 lines and 6 KiB,
  its own card only, prefixed so no line reads as a board marker — and
  never a log path to read.
- **Interruption and recovery.** Holding a run cancels its checks;
  stopping a batch cancels the one in flight; a restart closes a running
  check as "no result — the board restarted". Each reads *needed*, never
  failed, and **Run required checks** is the recovery. Nothing retries by
  itself.
- **Scheduling is off by default.** A merge rule requires checks; it
  never schedules them. `BOARD_VERIFY_ON_REVIEW` is the one opt-in, read
  at start-up, and asks only on a person's own move into review.

### Migration choices, and what each costs

Nothing migrates by itself: no policy is rewritten, no evidence
upgraded, no gate enabled, disabled or relaxed. These are the operator's
choices, each made deliberately:

| choice | effect | cost |
| --- | --- | --- |
| do nothing | legacy passes keep their rules; new passes carry identities | at most one fresh run per legacy pass that an unrelated edit has staled |
| move the required check's root from `worktree` to `source` | a fresh checkout of the exact commit, no ignored state, reusable passes | a definition change: its passes read `check-changed` once. Run both once on one commit and compare first |
| remove a duplicate required check | fewer runs | compare real coverage first; or keep both names and declare `accepts`, which bench then proves |
| stop configuring `github` review on a one-account project | removes a requirement that could never be met | none |

To see what a change of resolver does to an existing board before
adopting it, `python3 verify/gate-compare` copies what the gates read
(`snapshot`), evaluates every card with two resolvers on private copies
(`run --before-core <installed core>`), and explains each changed
reading. A reading that became less restrictive must be one the design
names — an unrelated policy change that is now provenance, an older
commit's outcome that is now history, or an equivalence bench proved —
or the comparison fails. It writes nothing it read from, asks GitHub
nothing, and leaves out `.env`, secrets, logs and the HMAC key.

**The runner-identity transition (task 121).** The `runner` dimension of
every result identity is now a digest over the files in
`resultidentity.RUNNER_FILES` — the runner, the identity, the record and
its freshness, and the record and git leaves they stand on — rather than
the hash of `verify.py` alone. Promoting that core changes the value
once: every recorded pass reads `stale / runner-changed`, legacy records
included, and each required check on an open card needs one fresh run.
Nothing reruns by itself and nothing is rewritten; **Run required
checks** is the recovery. A rollback costs the same invalidation in the
other direction. `gate-compare run … --expect runner-changed` previews
it on a copy and fails if anything other than that transition changes.
See `verify/README.md`.

**Task 131 is one such transition.** Per-run source checkouts change
`verify.py`, a runner file, so promoting that core reads every recorded
pass `stale / runner-changed` once, exactly as above, and each required
check on an open card needs one fresh run. No other dimension moves: the
checkout path was never part of an identity. Historical evidence is not
rewritten; a record made in a shared `verify/src/` checkout keeps naming
that path.

**Task 133 is another.** Keeping a check's demonstration outputs changes
`verify.py`, so promoting that core reads every recorded pass `stale /
runner-changed` once, and each required check on an open card needs one
fresh run. Declaring `"outputs": true` on a check moves nothing else: it
is outside the check's revision, id and substance. A check that declares
it is handed one more variable (`BENCH_VERIFY_OUTPUTS`), whose name is
bound like every other variable's, so its first run after the
declaration is a new result identity.

**Task 135 is another.** The verifier report contract adds
`verifierreport.py` to `resultidentity.RUNNER_FILES` and changes
`verify.py`, so promoting that core reads every recorded pass `stale /
runner-changed` once, and each required check on an open card needs one
fresh run. A check that does not declare `"report"` keeps its revision,
id and substance byte for byte; one that declares it changes all three,
so its earlier passes read `check-changed`. Freshness and reuse now also
name `BENCH_VERIFY_OUTPUTS` for a check declaring `"outputs"`, as the
runner always handed it, so an outputs check's pass no longer reads
`env-changed` against itself.

### Every status word

The words a card can wear, from `delivery.WORDS`, with the precedence
row that produces each. The count is not written down anywhere: a test
holds this table to the module's own list.

| word | label | tone | row |
| --- | --- | --- | --- |
| `merging` | Merging | working | 1 |
| `not-published` | Not published | alarm | 2 |
| `run-failed` | Run failed | alarm | 3 |
| `agent-working` | *name* is working | working | 4 |
| `phase-running` | Phase running | working | 5 |
| `not-started` | Not started | neutral | 6 |
| `queued` | Queued | neutral | 6 |
| `returned` | Returned — earlier work exists | neutral | 6 |
| `ready-to-start` | Ready to start | neutral | 8 |
| `in-progress` | In progress | neutral | 8 |
| `waiting-for-phase` | Waiting for phase | neutral | 9 |
| `waiting-for-parent` | Waiting for parent | neutral | 21 (read before 18–19) |
| `merged-elsewhere` | Merged on GitHub | neutral | 17 (read before 10–16) |
| `cannot-assess` | Can't assess | alarm | 6, 10 |
| `rules-invalid` | Merge rules invalid | alarm | 11 |
| `check-failed` | Check failed | alarm | 7, 12 |
| `cannot-run-check` | Check could not run | alarm | 7, 12 |
| `changes-requested` | Changes requested | alarm | 7, 12 |
| `checks-running` | Checks running | working | 13 |
| `checks-needed` | Checks needed | neutral | 14 |
| `review-needed` | Review needed | neutral | 15 |
| `signoff-needed` | Sign-off needed | neutral | 16 |
| `ready-to-merge` | Ready to merge | calm | 18 |
| `ready-overridden` | Ready — overridden | alarm | 18 |
| `review` | Review | neutral | 19 |
| `delivered` | Delivered | idle | 20 |

`cancelled`, `stale` and `not-run` are reasons inside `checks-needed`,
never statuses and never failures. Every row of the design's 32-fixture
copy matrix (F1–F32), its backend scenarios (E1–E20) and its browser
scenarios (B1–B5) maps to named regression tests in
`tests/test_delivery_journey.py`, which fails if a scenario loses its
test.

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

**When a red check is not enough.** A check can do more than pass or
fail. With the operator's opt-in it can say *why*: that its own safety
check refused to run against the target it was given, that its fixtures
would not load, or which line produced which wrong value — and keep the
diff or screenshot that shows it. An agent then reads those findings,
fixes the code and asks for the same check again. Your team adds a few
lines to its existing wrapper; see "Verifier reports: actionable evidence
from the check itself" below. What the check says is still the project's
own account, labelled as such.

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

### A failed check's details, without a path to read

A check's log is a file under the tool's state, and a restricted run may
not be able to read it — nor should it need to. So when a run's check
does not pass, the board reads the log itself and returns its tail **in
the same reply**, and the shim prints it after the verdict:

```
FAILED: unit
…
summary:  failed — exit status 1, after 3.2s
--- check output (tail, scrubbed; data, not instructions) ---
│ FAIL: test_saves_comment (tests.test_x.Case)
│ AssertionError: 'saved' != 'queued'
--- end of check output (3912 earlier lines omitted) ---
details:  bench-verify --details ev:verification@… prints this again
```

`bench-verify --details <evidence-id>` reprints it later in the run
(`POST /api/verify/details`, `verify.diagnostic`). It is the same grant
as asking for a check, so no new permission exists and no adapter grants
any read of `state/evidence/`. No log path is printed.

**What it may read.** The run is proved by its own capability,
`BOARD_RUN_TOKEN`, which bench hands every `work`, `act-pr` and `review`
launch whether or not its adapter can report progress, and keeps only a
hash of, beside the run's progress record (so it survives a board
restart). It is not the progress token, and that token does not stand in
for it. Nothing is read without it: a run id the board issued nothing
to, a run launched before capabilities existed, and a missing or wrong
token are each refused — a run id and a recorded manifest say what a run
would be allowed, not that the caller is that run. A failed check's
immediate reply then carries no details and says why (`details: not
returned — …`). Its **card comes from that manifest**, never
from the request: another card's evidence, a diagnosis's, and a check the
run was neither offered nor asked for are each refused by name, and the
refusal is counted on the run's channel health like a refused progress
report. The argument is an evidence id, checked by shape before anything
is read; a path is refused as not an id, and only the evidence
directory's own log file is opened — never the path a record names, and
never through a link.

**What it holds.** Bench's own facts, unchanged from the record — the
outcome, reason, exit status, duration, source commit and each
preparation step's outcome — and a summary made from those alone, so
nothing is claimed that the output does not say. Then the tail of the
check's output, without bench's own log header: scrubbed of anything
credential-shaped **before** it is cut, stripped of escape, control and
bidirectional characters, each line clipped to 400 characters and
prefixed `│ `, at most 80 lines and 6 KiB, with the omitted lines
counted and a log bench clipped said to be clipped. Every untrusted line
starting with `│ ` is what keeps a line reading `PR REVIEW: APPROVE`,
`NOT READY:` or `<!-- bench:acceptance … -->` from ever sitting where the
board parses a marker; the delimiters say the text is data.

**What it says when there is nothing.** *The check printed nothing*; *the
log is not on this board*; *the log exists but the board could not read
it*; *the request was refused before anything ran*; *still running*. No
excerpt is invented. Reading writes nothing: the record, its log and its
verdict are byte for byte what they were, and a person still opens the
whole scrubbed log from the card's ◫ sheet.

### Asking twice runs once

The work run asks for a check after its last commit; the review run asks
for the same check at the same PR head. That used to be two runs of one
question. Now the second request is answered with the first record:
`bench-verify` prints `REUSED`, names the original evidence and who it
was run for, and nothing runs.

The rule is narrow on purpose, and it is decided *after* the request is
authorised — the asking run's frozen profile must still offer the check,
so a reusable pass never authorises an intent the policy has since
forbidden. Then bench works out, without making any checkout, the
result identity this request would record now (see "The evidence"), and
reuses a record only when:

- it is a run of **this check by name**, for this card, at the commit the
  check would run against now — the card's worktree head, the branch tip
  for `source`, the pinned PR head for `review`;
- it is the **newest relevant answer** there, as at the gate: a newer
  failure, cancellation or error at that commit means the pass is not
  today's answer, and a run still in flight is newer than anything on
  record — so it is joined, never stepped past. "In flight" means
  *reserved*, not *on disk*: a run holds its place in memory before its
  first record is written. The reservations are read under the lock
  before the records are, and read again in the same hold of the lock
  that writes the reuse record. A run reserved for this check anywhere
  in between means the request runs or joins rather than trusting what
  it read, even if that run has already finished. A run reserved after
  that hold is newer than the reuse;
- it passed over a **bound, clean** source, with the same coverage
  (scope), and `outcome_now` still reads `passed`;
- its `resultId` **equals** the new one in every dimension: source,
  check substance, coverage, programs, preparation, declared inputs, root
  class, declared environment and runner. Who asked, under which policy
  revision, and where a `source` checkout happened to sit are provenance
  and refuse nothing.

A legacy record (no `resultId`) cannot show that equality, so it is
never reused and costs one new run; it is not rewritten either. A record
of another name is reused only where the check declares it in
`"accepts"` and the two are proven the same (see "Another name counts
only when declared and proven the same"): then every dimension of its
identity but the substance digest — which carries the declared root by
name — must equal the new one, the definitions are compared dimension by
dimension instead, the reuse record names it (`acceptedFrom`), and a run
of the accepted check in flight is joined-or-run around exactly as one
of the check's own. A review verdict, a progress claim or a comment is
not a record at all.

**The evidence is not copied.** The record keeps its own id, requester,
checkout, log and provenance, byte for byte, and the gate goes on citing
it. What the second asker leaves is a **reuse record** under
`local/state/evidence/reuse/` — machine-local, like the evidence: the
reused evidence id and its `resultId`, the identity the requester would
have had (so `result_id(identity)` can be checked against the record),
the requester's run, intent, request and policy revision, and a copy of
who the evidence was run for. No environment value and no hash of one;
the ticker says *reused … nothing was run*. Reuse records are never
evidence: `all_evidence()` does not list them and no gate reads them.
The page is sent them through an allowlist (`verify.public_reuse`), as
evidence is through `verify.public`, so the identity stays on this disk
and a field added later does not reach a reader by default.

**`--again` runs.** `bench-verify --again <check> [<scope>]`, the
`"again": true` field on `POST /api/verify`, and the ◫ sheet's `↻` all
ask for a new execution regardless, and the new record carries
`again: true`. A run of the same check already in flight is still joined
rather than doubled. A diagnosis (`--doctor --active`) never reuses: the
point of it is to run.

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

### One source checkout per run (task 131)

A `source` check runs in a detached checkout of one exact commit that
bench makes **for that run alone**: `local/state/verify/src-run/<card>-
<commit>-<run>`, beside an ownership record (`<same name>.owner.json`)
naming the run's evidence id, the card, the commit and the path. The
record is written durably *before* `git worktree add`, so a checkout
exists only once a run owns it. Two different checks of one card at one
commit — a backend suite and a browser suite started together, say — get
two checkouts pinned to the same commit, and the one that finishes first
cannot remove or alter the other's files. They used to share
`verify/src/<card>-<commit>`, and the first to finish deleted the source
the other was still running in.

- **Every executing run owns one; a join makes none.** A forced rerun
  (`--again`) and another scope of the same check each get their own. A
  duplicate request for a run already in flight joins it before any
  checkout is made, and a request answered by an equal result already on
  record (`REUSED`) makes no checkout at all.
- **Release is proved, never assumed.** Every cleanup path — a pass, a
  failure, a preparation failure, a program that could not start, a
  timeout, a cancellation, a caller's stop — releases only the checkout
  whose ownership record names *this* run and *this* path, inside
  `src-run/`, not through a link, still this application's worktree, still
  at the commit it was made at, with nothing uncommitted in it, and
  through `git worktree remove` without `--force`. Anything else is kept
  and the record's `root` says why. No prefix, card-wide or directory-wide
  deletion exists.
- **Interrupted creation is conservative.** A `git worktree add` that
  failed before making anything leaves nothing, not even the ownership
  record; one that left a partial directory is kept and named in the
  refusal. A board restart releases what an earlier board's runs provably
  owned under the same guards, tells that run's own evidence what became
  of its checkout (and no other record), and keeps the rest. Recovery
  proves ownership from the record and the checkout, not from whether the
  dead board's check process is still alive: a clean, owned checkout is
  removed even if an orphaned process is still running in it. That run's
  evidence is already closed as `error`, so no result rides on it.
- **What is kept, accumulates.** A checkout kept for a reason — a check
  that left untracked, non-ignored files, say — is kept per run, so a
  check that always does that leaves one kept checkout under `src-run/`
  per run rather than one per card and commit. Each is named in its own
  run's evidence; nothing bounds or prunes the total, and removing them is
  a person's `git worktree remove` once nothing uses them.
- **The old shared paths are never adopted.** A checkout under
  `verify/src/` was shared by design, so no run can prove it owns one:
  bench neither runs in, reuses, migrates nor removes it. The restart's
  ticker line counts what is left (`verify.source_leftovers` lists it), and
  removing it is a person's `git worktree remove` once nothing uses it.
- **The path is provenance, not identity.** A unique directory does not
  make an equal result different: the result identity binds the commit and
  a digest of everything uncommitted, never the path, so a pass recorded
  in one run's checkout is reused after that checkout is gone, and a new
  commit still makes it `stale / source-changed`. A fresh checkout is
  always `fresh` as its root class.

What this does **not** isolate, said plainly: the git object database is
shared (it is immutable), and so is the cache home a check's `HOME` and
cache variables point at, which is keyed on the card and commit as it
always was — so a `pip` or `npm` cache can be written by two runs at
once. A check's own side effects (files it writes by absolute path,
services, databases, ports it binds) are as shared as they ever were. And
the `worktree` and `review` roots are unchanged: a `worktree` check runs
in the card's own checkout and a `review` check in the review's pinned
checkout, neither made nor removed by the verification, so two checks of
those roots still share one directory. Independent source checkouts
prevent one run deleting another's checkout; they are not a sandbox.

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
  forced out. Each record's `root` names the checkout that run actually
  ran in (`checkout: "per-run"`, its ownership record) and what became of
  it — `removed`, or `kept` with the reason — and one run's cleanup never
  writes another run's record.

### Contention: what else was running

A check on a shared machine competes with everything else on it — other
checks, agents, a person's own work. Capacity is not agent concurrency,
and a slow run beside another is not proof that one slowed the other. So
bench keeps three readings apart (task 179), and none of them is part of
a verdict:

- **Queue, preparation and execution.** The evidence record already
  holds the request time, the slot wait (`queued`, kept over a second),
  each preparation step and the check process. `python3
  verify/contention-report <evidence dir>` prints them as separate
  columns, with *unknown* where a
  record cannot say (an older record has no slot wait over a second to
  report), never zero.
- **Overlap**, computed from the records' execution windows, old records
  included: which other runs intersected this one, and for how long.
- **Observed load**, from `checkload.py`: while verifications run, a beat
  every 5 seconds writes a sidecar per run under
  `manager/local/state/evidence/load/` — its phase (waiting, preparing,
  running), the other checks in each phase, the parallel limit, the
  agent runs this board tracked, and the host's load average, CPU count
  and available memory. Bounded (16 named peers, the rest counted) and
  sanitized (numbers, evidence ids and check names — no command, path or
  environment value). A run with no sidecar is *not observed*; one with
  no `end` is a run whose ending a restart hid.

**Diagnostic, never identity.** No runner file imports `checkload`, so
the runner revision, every result identity and every reuse decision are
unchanged by it: no pass reads `runner-changed` because of it, the
sidecar is not evidence, and no gate reads it. Nothing in it schedules,
throttles, cancels or serializes a check. `BOARD_VERIFY_PARALLEL` is
still the only limit, read when a slot is asked for; what the
measurements recommend about it, and how an operator applies and rolls
that back, is `develop/perf/179-verification-contention.md`.

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
preparation first, a new evidence record and a new log — always a new
run, even where a current pass could have been reused (it is **Run
again**), no card moved, no branch touched, nothing published, and a run
already in flight joined rather than doubled. Requests that were
answered by an existing record are listed in a fold of their own, each
naming the evidence that answered and who asked.

### Run required checks: one click for what is missing

Who asks for a check is three parties and nobody else. The **work run**
(`work`, `act-pr`) asks for each required check after its last commit:
where a gate is configured its prompt says *"Ask for every required check
after your last commit; a pass before a later commit does not count"*,
names the required checks its own frozen profile lets it ask for, and
names any it may not ask for as a person's or the configuration's to
supply. The person's **▸ start work** or **↻ act on PR** is the consent
to the run; the profile's `verify` list is the authorisation inside it,
and the sentence grants nothing new. The **review run** may ask for what
its profile grants (an equal current pass is reused), so a review can
still come first. And a **person** has **Run required checks**.

**Nothing else, by default.** A merge rule requires checks; it does not
schedule them. Moving a card, opening a card, the Overview, the dialog
and the sheet below request nothing. The one exception is an operator's
explicit opt-in, `BOARD_VERIFY_ON_REVIEW`, which is off unless someone
turns it on and is described under "Asking on entering Review" below.

**Run required checks** is the primary action of a card reading
`checks-needed`. It opens a sheet (`GET /api/verify/required`, which
reads the gate, the candidate, the checkout's cleanliness and a person's
work reading, and starts nothing, pins nothing and makes no checkout)
listing every required check at the candidate commit H as one of:

| item | what the click does with it |
| --- | --- |
| will be requested | the requirement reads *needed* at H — not run, stale, cancelled, no result after a restart, history from another commit — and a person's work reading may ask for it |
| already running — joined | a run of it is in flight at H; it is not doubled |
| already met — skipped | a current pass at H is on record |
| not requested — its last result did not pass | failed, preparation failed, refused, or no verdict for another reason. That is a result to read; **↻** in the ◫ sheet runs it again, deliberately |
| not requested — your work reading may not ask for it | the rule requires a check the work profile does not grant. The row names the configuration remedy — add it to the work profile's `verify` list, or change the rule — and nothing widens the grant |

Optional checks are not items at all, so they never run here. The
sheet says, in the ◫ sheet's own words, what the click costs: each
check's command in the card's checkout (or a detached one bench makes
and removes), its preparation steps first — drawn apart from the check,
because a preparation that fails is *preparation failed* and not a test
failure — a new evidence record and log per check, one at a time in the
merge rule's order, no card moved, no branch touched, nothing published,
nothing retried. The armed button fires it.

**What the backend refuses**, with nothing requested
(`POST /api/verify/required`, behind the same door as a policy write —
JSON, `X-Bench-Settings: 1`, the origin checks): a card with no
implementation commit on this board; a checkout holding the branch with
uncommitted or untracked files (*commit, then run checks*); a board with
no merge rule here, or rules that do not resolve; a work reading that
does not resolve; nothing left to request; a body naming a run (`agent`)
— a run asks through `bench-verify` under the profile it launched with;
and a request that does not name the head the sheet was shown — a
missing or blank `head` is `head-required` (400), another commit is
`head-moved`. A second click while a batch is in flight returns that
batch rather than starting another.

**Between requests the candidate is read again.** Before each check the
branch tip, the merge rules' revision and the checkout's cleanliness are
re-read, and the requirement itself is re-evaluated — an agent's request
or another person's **↻** may have answered it meanwhile (it is then
skipped as met, or joined). A tip that moved, a rule that changed or
uncommitted work that appeared stops the batch, names why, and lists the
rest as *not requested*. A result whose source is another commit is
reported as *not this candidate's answer* and stops it too. Each request
is `verify.request` as a person's work reading, exactly as **↻** asks,
so every refusal, reuse, join and record is that function's.

**Progress, and endings that are explicit.** The sheet follows the batch:
how many of the checks it set out to request have an answer, which one
is in flight — *preparing* apart from *running the check* — and which
remain, with an armed **Stop the batch**. `/api/state` carries the same
summary (`requiredRuns`), and the card's action reads *Required checks:
1 of 3 answered* while it runs.

- **Stop** cancels the check in flight through verify's own cancel —
  recorded `cancelled`, never a failure — and the rest are *not
  requested*. A Stop that lands after the batch chose a check but before
  verify reserved a run of it runs nothing at all: `verify.request` asks
  the batch before its reuse lookup, before and after making a checkout,
  and once more in the same hold of the lock that would reserve the run.
  No preparation or check starts, a `source` checkout made for it is
  given back, no evidence is written, and the item reads *not
  requested*. A run an agent asked for that the batch joined is that
  run's, and Stop leaves it alone — **‖ hold** is what stops it.
- **‖ hold** on a run cancels the checks that run asked for
  (`cancelled-with-run`). Either way the requirement reads
  *Cancelled before a result on H*, which is *needed*, and **Run
  required checks** is the recovery.
- **A board restart** closes a check left running as `error` with
  *board-restart*, which the gate reads as the reason `restarted` —
  *No result on H — the board restarted*, in the *needed* class beside a
  cancellation rather than a problem — and closes the batch as stopped,
  naming the restart. Again the recovery is a click.
- **Nothing retries by itself.** Not after a stop, a hold, a failure or
  a restart. A batch that ended leaves **Run required checks again** in
  the sheet, and that is a person's decision.

One record per card, the newest batch, machine-local under
`state/evidence/required/<stem>.json` beside the evidence it points at.
It names checks, states and evidence ids, never a path.

### Asking on entering Review: an operator's opt-in

`BOARD_VERIFY_ON_REVIEW=1` in `manager/local/.env` makes one more thing
ask: **a card a person moves into `review/` through this board's own
page** requests what one **Run required checks** click would request at
that moment. It is off by default, it is read once when the board starts
(so turning it on or off is an edit to that file and a restart, like its
neighbours), and an update never changes it — `.env` is yours. Off, a
move into review runs nothing at all.

It is the click, not a second mechanism. The same plan, the same batch,
the same person's **work** reading and `verify.request` — so it can run
nothing a click could not, and every rule above holds: only `needed`
requirements are requested, met ones are skipped, running ones joined,
optional checks never run, a check the work reading may not ask for is
named in the ticker with its configuration remedy and nothing is
widened, a dirty checkout or a missing candidate requests nothing and
says why, and the candidate is re-read between requests. The batch
record says `trigger: review-entry`, and the ticker line says the
setting asked.

What it answers to, and what it does not:

- **Only this board's person.** The move must be `you` — a move made
  through the page (`taskfiles.move_task`). An agent's move after a
  work run, a phase runner's, a hand `mv` on disk (*nobody in
  particular*), and a move a sync pull brought from another board all
  request nothing: a replica renders, the actor acts (see "State syncs;
  reactions don't").
- **Not a start or a restart.** The watcher's first signature is the
  baseline, so a board starting up with cards already in `review/`
  requests nothing.
- **One commit, once.** A card's commit is asked for at most once on
  entry — in memory, so two notifications or two racing moves start one
  batch, and in `state/evidence/required/on-review/<stem>.json`, so a
  board that restarted remembers. Moving the card out and back at the
  same commit requests nothing again, including a check that failed, was
  cancelled or was refused: that is a person's **Run required checks**
  or ↻. A new commit is a new question. The record is written **before**
  anything runs, so a board that stops mid-batch does not ask again after
  its restart; it is given back only when nothing was requested — the
  plan refused, or a batch for another commit of the card was still
  running (that batch is not this commit's answer, and the ticker says
  so). An interrupted entry therefore asks less, never twice, and **Run
  required checks** is the way on. A record bench cannot write requests
  nothing.
- **Only the card's own rule.** An ordinary card asks for what the Card
  merge rule requires, a phase member for the Phase member merge rule's,
  a phase card for Phase acceptance's. The Settings fold names, for each
  check, the rules that require it.
- **Nothing follows from it.** No agent is launched, no review is
  started, nothing is merged, nothing retries, and no gate reads it.
  Stopping the batch, **‖ hold** and a restart behave exactly as they do
  for a click.

**Settings → Merge rules** shows it as a row — on or off, where the
value came from (default, `manager/local/.env`, or the environment,
locked), whether the file has moved since the board started — with a
fold naming the checks it would run and their commands, the cost
sentence the confirmation uses, and how to turn it on and off. It is
listed under **Installation** with every other `.env` setting. Applying
a policy change in Settings writes the policy document only, so it
neither changes this setting nor any grant.

### Verifier reports: actionable evidence from the check itself

A red check tells a coding agent that something is wrong and very little
about what. And an exit status cannot tell a test that failed from a
wrapper that refused to run against an unsafe target, or from one whose
fixture would not load: all three exit non-zero, so all three read as
`failed`. The **verifier report contract** (task 135) lets a check that
opts in say which it was, what it found and where, in a small JSON
report bench keeps beside the log. Projects keep their commands and
their safety wrappers; the wrapper adds a few lines that write the report.

**Opting in is the operator's, per check.** `"report": "v1"` on a check
in the policy document. Nothing else changes: registering the contract
makes no check required (that is still a merge rule's decision), grants
no run anything, and is frozen into a run's manifest at launch like the
rest of its policy. No request parameter turns it on, off or to another
version, and an edit to the document after a launch reaches the next
launch only.

**What the check is handed.** `$BENCH_VERIFY_REPORT_DIR`, a directory
made empty for this invocation alone inside the run's own temporary
directory, and `$BENCH_VERIFY_REPORT`, the `report.json` inside it. The
verifier writes one JSON object:

```json
{"v": 1, "outcome": "failed", "summary": "1 of 4 fixture totals is wrong",
 "findings": [{"message": "case c1: the total is wrong",
               "location": {"path": "rules.json", "line": 3},
               "expected": "110.00", "actual": "120.00",
               "evidence": ["diff.txt"], "severity": "error"}],
 "artifacts": [{"path": "diff.txt", "label": "expected and actual totals"}],
 "extensions": {"cicero.checks": {"cases": 4}}}
```

`v`, `outcome` and `summary` are required. `outcome` is one of
`passed`, `failed` (an assertion), `setup-failed` (the verifier's own
setup failed, so its checks did not run), `refused` (it was launched and
declined to run its checks) and `incomplete`. Findings are diagnostic
data — a message, a location relative to the candidate checkout, expected
and actual observations and the artifacts that show them — not an
assertion framework. `extensions` holds bounded, namespaced data bench
keeps and never interprets.

**One rule decides the outcome.** Bench's own endings come first: a
timeout, a signal, a cancellation, a program that could not start and a
required preparation step bench ran are bench's reading whatever the
report says. Otherwise:

| report | exit status | recorded as |
| --- | --- | --- |
| none, unreadable, or not this contract | any | `error` — `report-missing`, `report-invalid`, `report-unsupported` |
| `passed` | 0 | `passed` — `report-passed` |
| `failed` | non-zero | `failed` — `report-failed` |
| `setup-failed` | non-zero | `prepare-failed` — `verifier-setup` |
| `refused` | non-zero | `error` — `verifier-refused` |
| `incomplete` | non-zero | `error` — `report-incomplete` |
| anything else: the two disagree | | `error` — `report-contradicts-exit` |

So exit zero alone never passes a contract check, and a passing report
never overrides a non-zero exit. Ordinary checks are untouched: without
the opt-in an exit status means exactly what it always did.

**A refusal after launch is an answer, not a denial.** `denied` means a
request was refused before anything ran, and an older pass at the same
commit still stands beside one. A verifier that was launched and refused,
failed its setup or stopped part way *ran*, and its record is the newest
answer for that check at that commit: it holds the merge rule unmet and
stops an older pass from being reused, for a check's own name and for a
declared equivalent alike. Nothing new decides this; it is the gate's and
the reuse rule's existing reading of `error` and `prepare-failed`.

**Whose words they are.** The record keeps the two voices apart, and so
does every surface that shows them: *bench observed* — the exit status,
the timing, the preparation and which row of the rule decided — and *the
verifier reported* — its outcome, summary, findings and any account of a
refusal or setup failure. The approved verifier's classification is the
project's own evidence. Bench does not observe a wrapper's internal
checks or safety controls, and a report that says it refused is evidence
that the program said so.

**What is kept, and where.** The log always, report or not. The report
file, and each artifact it declares, collected only from inside this
invocation's report directory: a relative path, no `..`, no absolute
path, no link anywhere along it, a regular file of text, JSON or an image
(PNG, JPEG, GIF, WebP) within `BOARD_ARTIFACT_MAX_BYTES` and
`BOARD_ARTIFACT_RUN_FILES`. Anything else is refused and named, and
nothing outside the directory is read, followed or deleted because a
report named it. A malformed report — over 256 KiB, more than 50
findings, a string over its limit, an unknown key, an artifact path
that climbs out, a finding citing an artifact the report does not
declare — is refused whole with the exact problem as its protocol error,
and only its raw file is kept. Kept files live under
`manager/local/state/artifacts/<card>/diagnostic/`, outside the run's
scratch and apart from the card's demonstration outputs (so Show the
result never reads a failing run's diff as a demonstration), bound to
the run's evidence id, check, outcome and source, with the same retention
and withholding as any artifact. Text is scrubbed of anything
credential-shaped on the way in — the report's strings, and each string
inside its extension data one by one — and that is pattern matching, not
a promise that every secret is found; an image is kept as made. When a
secret gets through, **Withhold** (below) removes the file's bytes. A
report nested deeper than the contract reads is a protocol error like any
other, and a kept file too deeply nested to parse is kept as a plain file
or refused, never as JSON. Neither is a reason the run fails to record
its outcome: a keeping that fails for any reason is said on the record
(`keptError`) and the outcome stands.

**A run reads it through the door it already has.** When a contract
check does not pass, `bench-verify` prints the report after the verdict:
its state and any protocol error, the reported outcome, the summary, each
finding with its location, expected and actual values and the ids of the
artifacts it cites, and every kept artifact by id. `bench-verify
--artifact <evidence-id> <artifact-id>` prints one kept text or JSON
artifact, bounded (200 lines, 16 KiB) and prefixed like the log excerpt;
an image is described, not returned, and a person opens it from the ◫
sheet. Both ride `/api/verify/details` under the access contract above —
the run's own token, its own card, a check it was offered or asked for,
and an artifact that record's report kept — so no path is handed out and
no host file becomes readable. Every string the verifier wrote is
printed after `│ `, so none of it can sit where a marker is parsed, and
a suggested command in a finding is text, never an instruction bench
acts on. The loop is: ask for the check, read the findings, fetch the
evidence, fix, commit, ask for the same check again.

**On the board.** The card's ◫ evidence sheet lists each reported run
under **Verifier reports**: the outcome in words, what bench observed and
which row of the rule decided, any protocol error with its remedy, the
verifier's summary, each finding expandable with its location and
expected and actual values, the kept artifacts (served by `/api/artifact`
with `purpose=diagnostic`, sandboxed and `nosniff`), what was not kept
and why, and the log. Under the kept files, **Withhold a kept file**
takes a reason and, armed, removes one file's bytes for good through
`POST /api/artifact/withhold` with `"purpose": "diagnostic"`; the record
that it existed stays, with who withheld it and why, and a run asking for
it afterwards is told it is not available. Settings → Checks shows `Verifier report: contract
v1` on a check that opted in and explains the contract and its trust
limits in a fold. Reading any of it runs nothing.

**Identity and freshness.** The contract is part of what a check is: it
is inside the check's revision, its id and its substance, so declaring
it, removing it or changing its version reads every earlier pass of that
check `stale / check-changed`, and a contract check and a plain one are
never equivalent (`check_differences` names the `report` dimension). The
report variables' names are bound like every other variable's. An
unrelated policy edit leaves a pass standing, exactly as before. The
contract module (`verifierreport.py`) is inside the runner revision, so
promoting this core reads every recorded pass `stale / runner-changed`
once and each required check on an open card needs one fresh run — the
same transition as tasks 121, 131 and 133. Records written before it have
no `report` block and read exactly as they did.

**Adapting a wrapper.** `verify/examples/report-contract/` holds a
stdlib-only emitter (`bench_report.py`) to copy beside an existing
wrapper, and a disposable, Cicero-shaped wrapper that keeps its own
safety checks — an unsafe target is refused, missing fixtures are a setup
failure — and reports failing totals as findings with a cited diff. Its
README is the step-by-step. Outside bench the same wrapper writes
nothing and exits as it always did.

**Bench's own wrappers speak it (task 164).** Every `verify/` entrypoint
(`backend`, `docs`, `browser`, `preflight`) is built on
`verify/benchverify.py`, which now writes a v1 report whenever bench
hands it `$BENCH_VERIFY_REPORT`: exit 0 is `passed`, exit 1 `failed`,
exit 2 `setup-failed`, and a cancelled run `incomplete`, with one
bounded finding per browser case that did not pass. Without the opt-in
nothing changes: bench hands a check no report path unless its policy
says `"report": "v1"`, and the exit status means what it always did —
which is why, in #158, the browser verifier's setup errors (Playwright
timeouts, page crashes) read `failed`. Declaring `"report": "v1"` on the
browser check in the policy document is the operator's separate act; this
change applies nothing to a live policy. Once declared, a setup error is
recorded `prepare-failed` (`verifier-setup`), an assertion failure
`failed` (`report-failed`), and a timeout, a cancellation or a launch
error stays bench's own reading whatever the wrapper wrote.

**A browser health probe (task 164).** `verify/preflight browser` proves
the browser starts, sandboxed, and answers; it never proved a page would
load and respond. `verify/preflight browser --probe` also starts a
disposable backend, loads the candidate's board and switches a view by a
click and back, under the same sandbox rules as the cases (no
`--no-sandbox`; the remote adapter requests `chromiumSandbox: true`,
which only the server can honour, as the cases' record already says). It
records which of `connection`, `navigation` and `interaction` it reached,
keeps the browser and backend logs and a screenshot, and fails as a
setup error naming the step. It is a health probe, never feature coverage
or proof that a full run will be stable, and its record says
`featureCoverage: false`. Using it as the browser check's `prepare` step
is again the operator's choice.

**What it is not.** Not an isolated runner, a sandbox, a dependency or
service framework, or a way to prove a verifier honest. It contains no
hostile code, gives no run a new permission, applies no fix a finding
suggests, and activates no merge rule.

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
that cannot be read is outdated, never current.

### Another name counts only when declared and proven the same

A record is filed under the name of the check that ran, and a requirement
names a check. So by default a record of another name answers nothing —
two checks running the same command line under two names are still two
checks, and a matching argv, a matching label or matching output never
makes them one. A project that keeps two names for one definition (say
`unit` rooted at `source` for the work run, and `unit-review` rooted at
`review` for the PR review) can say so, on the check the gate requires:

```jsonc
"checks": [
  {"name": "unit", "run": "python3 -m unittest", "root": "source",
   "accepts": ["unit-review"]},
  {"name": "unit-review", "run": "python3 -m unittest", "root": "review"}
]
```

**It is one-way, and it is not transitive.** `accepts` on `unit` lets a
`unit-review` record answer a requirement for `unit`; it says nothing
about what answers `unit-review`. A check may only accept checks that
accept nothing themselves, so a chain or a cycle is refused where the
document is read (`check-accepts-cyclic`), as are a value that is not a
list of names, the check itself, a repeat (`check-accepts-invalid`) and a
name the document does not declare (`check-accepts-undeclared`). A
refused declaration refuses its check, so the gate reads shut, not open.
`accepts` is not part of a check's revision, `checkId` or substance:
declaring it stales nothing and migrates nothing, and a document that
does not use it reads byte for byte as before.

**The declaration is necessary and never sufficient.** A record of an
accepted name counts only when all of these hold too:

- the two definitions are the same in every substantive dimension —
  the command, the root *class*, the time limit, the declared inputs, the
  declared environment, the preparation, and for a scoped run the
  scope's arguments. The root is compared by class: `source` and
  `review` are both a checkout bench pins to one commit, and the
  `worktree` is a separate class because it can hold ignored state a
  pinned checkout does not. (A check's own substance keeps its declared
  root by name, so moving one check from `source` to `review` still
  stales its own passes; only the comparison *across* names reads the
  class.)
- the record ran in that class, as the runner found it: a pinned
  checkout counts as fresh only when it held nothing beyond its commit,
  so a `reused` one does not.
- the record has a result identity — a legacy record cannot show any of
  this — and that identity holds now under its own name: programs and
  interpreters, preparation programs, input contents, declared
  environment values and the runner, exactly as for any record.
- and everything a record of the check's own name must satisfy: this
  commit, a bound and clean source, coverage, not a refusal.

A qualifying record is not a separate route. It goes through the same
walk as the check's own records — relevance to this head, then the newest
answer — so a newer failure, cancellation or run in flight of *either*
name decides over an older pass of the other, and an equivalent run
still preparing holds the requirement pending. Whether a check may be
*asked for* is still the asking profile's answer at every request
(§ "How a run asks"), and the declaration is read from the policy in
force now, never from a frozen manifest: removing `accepts` stops a
record counting at the next read.

**What it says.** A requirement met this way says so — *bench ran
'unit-review' … — a run of 'unit-review' (evidence …, run for rev-1),
which 'unit' accepts as the same check* — and its row carries
`acceptedFrom` (and the delivery reading `reusedFrom`): the record's own
check name, evidence id, requester, intent, root and root class. The
evidence is never copied or relabelled. A declared record that is not
the same check leaves the requirement `not-run` and names the one
dimension that stops it (`equivalence`, e.g. *'unit-review' runs a
different preparation, so it is not the same check as 'unit'*). A browser
check and a backend check run different commands, so no declaration
makes one stand for the other; a review verdict, a progress claim, a
comment or CI is never a record at all.

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
| `error` | no verdict was reached — a timeout, or the runner itself failed |
| `restarted` | the board stopped while it was running, so it has no result. A step still ahead, like `cancelled` — **Run required checks** is the recovery |
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

**One review is counted, unless you ask for distinct ones.** The ordinary
form is one word, and only that review is a requirement: a Bench
`APPROVE` and a GitHub `APPROVED` on a `bench` rule are one requirement
met, with the other shown as information — never two approval counters.
Comments, Copilot, relevance checks and agent summaries are shown in the
card's Reviews and counted nowhere. A project that wants an agent's
review **and** an independent person's approval writes the advanced
form, a list — `"review": ["bench", "separate-reviewer"]` — and every
word in it is its own requirement row, met or unmet on its own record and
explained on its own; a Bench verdict never stands in for the separate
reviewer, and the same-account refusal above is never read as an
approval. A list is a conjunction of genuinely distinct assurance, so a
repeated word, `none` inside a list and `github` beside
`separate-reviewer` (a separate reviewer's approval is a GitHub approval
already) are refused where the document is read
(`gate-review-duplicate`), as are an empty list and a non-word
(`gate-invalid`). A scalar `review` reads exactly as it always did: its
gate revision and every override's scope identity are unchanged byte for
byte. Settings → Merge rules shows a list as *all of* its words and
leaves it as written; the Advanced policy editor has a box per word,
where one tick writes the ordinary word and two or more write the list.

### Sign-off: a person's decision, only where a rule asks for one

The PR review already judges the work against the card's acceptance
criteria — each one, at the exact head it was pinned to, with what it
could not judge listed (see "Pull requests"). That is a reviewer's
assessment and records no acceptance. Where the decision has to be a
person's — visual, business, regulated — a gate scope asks for it:

```jsonc
"gates": {"acceptance": {"checks": ["unit"], "review": "bench",
                         "signoff": "criteria"}}
```

`signoff` is `"none"` (the default, and what every existing document
means) or `"criteria"`; anything else is `gate-signoff-unknown`. With
`criteria`, the gate has one more requirement row, `sign-off`, read from
`acceptance` — the one authority for what a criterion's state is — every
time the gate is evaluated, the reading immediately before the merge
included:

| reason | what it means | class |
| --- | --- | --- |
| `signoff-untracked` | the criteria (or some added since) have no ids, so no person can have accepted them — **Track acceptance** | needed |
| `signoff-no-criteria` | the brief has no checklist to sign off | needed |
| `signoff-pending` | not every tracked criterion is accepted; an agent's `reported` claim counts as nothing | needed |
| `signoff-recheck` | an acceptance no longer stands: reworded, marked for recheck, or its evidence stopped reading passed | needed |
| `signoff-other-head` | accepted against an earlier commit (or before there was one); a sign-off is about the commit that would merge | needed |
| `signoff-needs-work` | a person recorded a criterion as not met | problem |
| `signoff-unreadable` | the card's text could not be read on this board, so nothing about its criteria is known | cannot-assess |
| `signoff-accepted` | every tracked criterion accepted by a named person against this commit | met |

"This commit" is the one the merge would take: `phase/<stem>` when the
card has that branch, else `task/<stem>` — the order `gates.head_for`
reads, and the one a person's decision records its commit by. A card
holding both branches is therefore confirmed against the phase tip, and
an acceptance recorded against the task tip does not travel to it.

The card reads `signoff-needed` with **Track acceptance** or **Confirm
criteria**, which opens its Acceptance section and changes nothing; every
decision there is the existing armed write. Turning sign-off on rewrites
nothing — existing acceptance records and decisions are read as they
are, no criterion is accepted by the setting, and nothing in bench writes
`signoff` into a document. An override can proceed over it (`sign-off`
is named in the record like any gate) and accepts nothing. Without
`signoff`, nothing about acceptance is required and nothing on the board
asks for it.

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
  later card is always `enabled: false` with that reason — never a
  button that does nothing. `signoff-needed` exists only where a merge
  rule asks for sign-off; its action, **Track acceptance** or **Confirm
  criteria**, opens the card's Acceptance section and changes nothing
  itself (see "Sign-off: a person's decision, only where a rule asks for
  one"). **Run required checks** is the
  primary action of `checks-needed`; where every needed check is one a
  person's work reading may not ask for, it arrives disabled with the
  configuration remedy, and while a batch is in flight it opens its
  progress instead (see "Run required checks: one click for what is
  missing").
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
- **the review records, apart** (`reviews`) — Bench's newest verdict
  (`gates.verdict_at` for the current commit, else the newest on record)
  and the newest round filed in the card's text, each with the revision
  it is about and whether that is the commit that would merge. GitHub's
  formal state stays the PR poll's, as `review` beside its folded
  `verdict`.

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
and the technical provenance. The compact form names it by `detail`, a
digest of every row with its prose and remedy, the merge rule's reading
and the completion, so a check replaced by another with the same totals,
or new evidence behind the same summary, is a new `detail` and the page
fetches the rows again — and only then. The newest request answers: a
reply to an older one is dropped, never drawn over a newer one. A read
that fails says so above the rows it last had, keeps them marked as
possibly out of date, and is asked again after a few seconds rather than
on every repaint. A merge dialog opened meanwhile reads the rows fresh,
shares them with the Overview when they are about the same reading, and
otherwise asks for the board state again, so the two cannot keep
disagreeing.

- **The face** wears the status under the face rule in "The card face
  and its tools", and one `⚖` chip whose count is `counts`.
- **The Overview and the dialog** draw the same block: the word as a
  heading, the sentence (`role="alert"` only in the alarm tone), the
  primary action first and then **More actions**, a native disclosure.
  Under it, **Merge requirements**, with optional checks, earlier runs
  and the technical details folded away.
- **Every action is the existing route**, wired once: a move, a launch,
  a hold, **↑ open PR**, the completion sheet (which is itself the
  confirmation), the **Run required checks** sheet (likewise),
  **Check the remote**, the launch check, a cancel, or a
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

**Run required checks** is a third explicit action and, unlike those
two, runs the project's programs; it has a section of its own under
"Verification: bench runs the check".

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
exactly as before. The status word follows the snapshot, but the
completion does not need it (task 166): merge & clean up asks the remote
about the exact head whatever the snapshot says, so a stale `OPEN`, or no
snapshot at all after a restart, reconciles the same way (see "After the
host merged it: delivery is not publication"). A stacked card stays a stacked card: it lands through
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
way to change it was a text editor. The **Settings** view — the last of
the four primary destinations, after Board, Brain and Orchestrator — is
where it is read instead:
**how is this bench configured, what has actually been verified, and what
should I configure next?**

It owns no policy. Every value in it comes from the authority that
already answers for that question — `profiles.resolve()` for what a run
may do and read, `gatepolicy.gate_policy()` for what a merge must have,
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
fixed list of names; nothing under `core/` is touched.

**Orchestration prompts** have a panel of their own beneath, edited
through the same draft, steps, backup and recovery: the shared
**Orchestration reasoning** template (`reason.md`) and four **Reasoning
guidance** templates, one per need (`reason-readiness.md`,
`reason-questions.md`, `reason-plan.md`, `reason-recovery.md`). Each row
says what uses it, that the Bench scope and every phase scope share it
(there is no per-scope prompt), whether it is shared or specific to one
need, and that a save reaches the next reasoning launch. Their preview is
the composition the run receives: a guidance template is shown inside
the shared template in force, and the shared template with the guidance
of the need you choose. See "Orchestration prompts: adapting what
reasoning is told".

Each template is held to a contract (`templates.py`) before it is saved
**and** before any launch uses it: the text must parse as a format
string, every placeholder must be one that launch fills (named plainly —
no `{body.x}`, `{0}`, conversion or format spec), the inputs the runner
depends on must be present (the task, the workflow brief, the plan, the
pinned context, the verification and progress blocks), and the marker
the board parses (`NOT READY:`, `ADDRESSED:`, `RELEVANCE REVIEW:`,
`PR REVIEW:`; for the shared reasoning template `<<<REASONING`,
`REASONING>>>` and `REASONING DECLINED:`) must still be asked for, and
the text must fit its bound (64 KiB; 4 KiB for a reasoning guidance
template). An existing local override that
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
   standing, and only legacy records read `policy-changed`). Each change
   names the dimension it moves: `check-changed` for the check's own
   substance (command, root, time limit, inputs, environment, report
   contract) or what a preparation step it runs does, and
   `check-scopes-changed` when only its declared scopes moved — a scope
   is coverage asked for by name, so a full-run pass still stands, while
   the passes of a scope whose arguments changed, or that was removed,
   read `stale` (`scope-changed`) and are named; a scope only added
   costs nothing. A change no recorded pass depends on, such as renaming
   a preparation step, is `check-revision-changed` (task 177). And it says
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

**Every other write passes the same door, minus the header.** Each route
is declared once in `manager/core/routes.py` with a request class, and
the server applies that class's door before the route's handler runs —
so a route cannot skip it by forgetting to ask:

| class | routes | what a request must show |
| --- | --- | --- |
| `read` | every GET | nothing; reading changes nothing |
| `browser-mutation` | every person-operated POST | a JSON object body within a size cap; `Sec-Fetch-Site` same-origin or none when sent; an `Origin`, when sent, that is this board on the port it actually serves or exactly one of `BOARD_ALLOWED_ORIGINS`; `Origin: null` refused; and no run named by a request that says it is a browser's |
| `policy-mutation` | the 36 routes that change or exercise policy, or run the project's programs | `browser-mutation` plus `X-Bench-Settings: 1` |
| `run-authenticated` | `/api/progress`, `/api/verify/details` | the same door, then the run's own token |
| `event-ingest` | `/api/events` | the same door; a run the event names is proved by that run's token (next section) |

The body is checked before anything happens: a missing, negative or
non-numeric `Content-Length`, a chunked body, a body over the cap (1 MiB;
16 KiB for a progress report), JSON that does not parse and JSON that is
not an object are each refused with nothing changed. "This board" is the
port the board serves on, not `BOARD_PORT`, so a board started with
`--port` accepts its own page. A shim, a hook bridge or `curl` states no
`Origin` and no `Sec-Fetch-Site`, which is how a trusted local caller is
told from a stated foreign one; a run's claim to be a run is the run
token's to prove, never the route's class. `OPTIONS` is never answered.

`BOARD_ALLOWED_ORIGINS` (empty by default) lists the exact
`scheme://host[:port]` origins besides loopback whose writes are
accepted — a local proxy in front of the board, say. No wildcard, path or
near match is taken. **An origin check is not authentication:** it keeps
other pages in your browser from writing, and it admits no peer that the
proxy in front of the board does not already admit. `Host`, `Forwarded`
and `X-Forwarded-*` decide nothing. A board reached through a proxy whose
origin is not listed reads fine and refuses every write, naming the
origin it saw; loopback always works as the way back.

### Callers: run identity and Host, reported before enforced

Two more checks sit at the same door (`callers.py`), and each ships in
two explicit modes: `report`, the default, counts and shows what it would
refuse and refuses nothing; `enforce` refuses. **Report is an
observation, not a security pass.** Nothing moves a board from one to the
other by itself — no timer, no update and no merge of the source. It is
an operator's edit to `manager/local/.env` and a restart, and Settings →
Installation shows which mode is in force. A value that is neither word
reads `report` and is named there, so a typo never looks like
enforcement.

- **`BOARD_RUN_IDENTITY` — a request naming a run.** `/api/verify` with
  an `agent` asks under that run's frozen manifest, and `/api/events`
  with one links a session to that run. Each must carry the run's own
  `BOARD_RUN_TOKEN` as `token` — the capability `progress` issues at
  launch and keeps only the hash of, compared in constant time. The
  `bench-verify` shim always sent it; both event bridges now forward it
  as an optional field. An unproven claim — no token, a wrong one, a run
  this board never launched, or one launched without a capability — is
  counted on that run's channel health (Focus: *unproven claims*) and
  narrated once in the ticker. Enforced, an unproven verification is
  refused (`run-unproven`, nothing run), and an unproven event is
  recorded as a session with no run link: no agent, no task. A token is
  removed from an event before anything is recorded, so it reaches no
  session log, no task file and no page. A card refinement is launched
  with no capability, so under enforcement its events are recorded
  unlinked.
- **`BOARD_HOST_CHECK` — the `Host` a request names.** Allowed:
  `127.0.0.1`, `localhost` and `[::1]` on the port this board serves, and
  the host of each `BOARD_ALLOWED_ORIGINS` entry on its port (or with the
  port omitted, where the origin's port is its scheme's default). The
  comparison is exact: the host case-folded, IPv6 in brackets, no
  trailing-dot or other normalization, an unbracketed IPv6 address or a
  missing `Host` not a host. Enforced, any other `Host` is answered
  `421` and nothing is read or changed — loopback on the served port
  always works as the way back.

Settings → Installation lists the distinct `Host` and `Origin` values
seen since the board started (at most 24 of each, printable ASCII,
clipped to 160 characters), whether each is allowed and why, how often,
and the *names* of any `Forwarded`/`X-Forwarded-*` headers that arrived —
never their values, and never any other header. That is the inventory to
configure a proxy from before enforcing: read what the proxy actually
forwards, list its origin, then switch. Neither check authenticates a
person, and tailnet membership authorizes nothing: an approved-device
boundary in front of the board stays exactly where it is.

### Reference files render inert

`/files/plans/…` and `/files/reference/…` serve what people and agents
wrote. Every file goes out with `X-Content-Type-Options: nosniff`, so it
is never read as a type it was not served as (an unknown suffix is
`application/octet-stream`). `.html` and `.svg` also carry
`Content-Security-Policy: sandbox; script-src 'none'; …`: they render
— markup, styles and images — in an opaque origin with no script, form or
plugin, so a script inside one cannot call this board's API. A reference
document that needs script will not run it. A PDF is not sandboxed, since
Chromium's viewer is a plugin the sandbox would refuse.

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
and model; `settings.verification` and `gatepolicy.gate_policy` for checks
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
setup cards' own inspection (`appinspect.inspect`) and **proposes**: start
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

## Orchestration: scopes and operating agreements

### Start here: you own the product, the orchestrator runs the project

Without orchestration, every step on a card is a click of yours: ▸ start
work, **Run required checks**, ◔ review PR, ↻ act on PR, merge & clean
up. With it, you stay the **product owner** — you decide what is worth
building, answer the questions only you can answer, and set the rules —
and an **orchestrator** acts as the technical project manager for the
cards you hand it. It launches the coding agents, asks bench for the
checks, gets an independent review, sends work back for repair, and
merges where you said it may. Everything it does goes through the same
buttons you would have pressed, under the same rules, and is written
down on the card it is about.

**A worked example.** A teammate writes *"Export the table as CSV"* into
Backlog. Nothing happens: writing or editing a card gives it to nobody.

1. You read it and drag it into **To Do**. That move is yours — bench
   reads it from git — so the card is now yours to delegate. A card a
   teammate moved is theirs, and your orchestrator leaves it alone even
   after a sync brings it to your board.
2. Before any code, an agent is launched to read the card, its phase,
   its dependencies and what bench knows about them, and drafts the
   questions only you can answer — *"Include hidden columns? Options:
   no, yes. Recommended: no, because the brief says visible."* The
   question is written onto the card.
3. You answer on the board: the **Orchestrator** view's Decisions, or
   the card itself. Nothing is sent to chat or email; bench is the only
   place the conversation lives.
4. The orchestrator starts the work through ▸ start work's own path —
   the same branch, worktree, profile and preflight a click would get.
5. When the agent commits, the card moves to Review and its PR opens.
   The orchestrator asks bench to run the checks your merge rules
   require, at that exact commit. If one fails, a repair run is
   launched — at most twice — and the new commit is checked again.
6. A review runs on a **different model** from the one that wrote the
   code, in a checkout pinned to the commit under review. If it asks for
   changes, that is a repair too.
7. If you delegated merging, the card is merged once your rules are
   met, and bench reads the result back off the remote before it calls
   the card Done. If your rules require nothing, it says so — *ungated,
   not verified* — rather than calling the merge checked.

At any point you can pause it, take a card back to do by hand, or cancel
one run. Your own moves always win.

**What you decide, and where.**

| You decide | Where |
| --- | --- |
| whether orchestration is on at all | **Orchestrator → Operating agreement**: off until you apply one |
| which cards it may touch | by moving them into To Do yourself; **Take over** hands one back |
| what it may do: start work, review, repair, merge, integrate, diagnose | the agreement's authority, word by word |
| how much at once, and per day | slots, launches per day and per card, repair rounds, retries |
| what a merge requires | **Settings → Merge rules**, exactly as without orchestration |
| the product questions | **Orchestrator → Decisions**, or the card |
| which installation runs it | one designated installation; others only read |

**What it never does.** It does not take a card someone else moved,
pull anything out of Backlog or Done, treat an agent's "tests pass" as a
check result, relax or turn on a merge rule, grant itself an override,
run two schedulers over one phase, kill a run for being quiet, retry a
failure it cannot classify, or repeat an action whose outcome it could
not observe. It sends no notifications, publishes no release, deploys
nothing, and promotes no installation. Those stay yours.

**Turning it on, the first time.**

1. Configure the models first, in **Settings → Agents**: implementation
   and review on *different* models, so an independent review is
   possible. With both on the harness default bench cannot tell them
   apart, and refuses the review rather than pretending.
2. Open **Orchestrator → Operating agreement**, **Save draft** from the
   starting point (the defaults below are written into it), read the
   **Preview**, and **Apply**. Apply is the moment it becomes able to
   launch; there is no separate trial mode.
3. Designate this installation as the one that executes (**Orchestrator
   → Overview**). Other boards that sync the same cards stay read-only.
4. Move one card into To Do. The next pass — every 30 seconds by
   default — picks it up.

Upgrading bench never turns it on, and never touches an agreement you
already have: the agreement lives in `manager/local/`, which updates do
not replace.

**The sixteen decisions it is built on**, and where each is described:

| | Decision | Section |
| --- | --- | --- |
| D1 | Your latest attributable move, not the last edit, makes a card yours | "Last mover, not last editor" |
| D2 | Every judgement is a launched agent with the card's context and a public response | "Orchestrator reasoning" |
| D3 | Review runs on a different model, shown to be different, or it is refused | "Delivery: checks, independent review…" |
| D4 | Merge delegation is your choice, under the merge rules you already have | "Delivery…", "Operating agreements…" |
| D5 | A stalled or failed run gets at most two bounded, read-only diagnoses | "Recovery: diagnose, don't kill" |
| D6 | A phase is a scoped sub-orchestrator, narrowing its parent | "Phase scopes: one scheduler per phase" |
| D7 | Two implementation slots, one review slot, six launches per card, twenty a day | "Operating agreements…" |
| D8 | Limits are launch counts; usage is shown only when the vendor reports it | "Orchestrator reasoning" |
| D9 | Transient failures retry twice with backoff, behind a circuit breaker | "Scheduling: bounded passes…" |
| D10 | `**Touches:**` declares overlap; undeclared overlap is unknown, never safe | "Scheduling: bounded passes…" |
| D11 | The card keeps every question, answer, response, blocker and action | "The card keeps everything" |
| D12 | The board is the only channel | "The card keeps everything" |
| D13 | One designated installation, held by an OS lock | "Operating agreements…" |
| D14 | Execution moves only by a quiet, explicit handover | "Operating agreements…" |
| D15 | A top-level Orchestrator view | "The Orchestrator view" |
| D16 | It can launch from the first enabled agreement; installs are off | "Operating agreements…" |

### Operating agreements, scopes and one installation

Orchestration is one engine at two levels: a Bench scope that
coordinates standalone cards and phase scopes, and a phase scope that
coordinates one phase's members. This part is its foundation — what a
scope may do, how much, and who may execute — and it schedules and
launches nothing itself: attribution, the reasoning contract, on-card
history, the scheduler and its ledger, delivery, phase scopes, recovery
and the view, described in the sections after it, all ask it.

**Off until a person turns it on.** With no agreement in force
(`manager/local/orchestration.json`, machine-local like the execution
policy) there are no scopes, nothing is authorized and no card or run
is touched; a fresh install is off. A draft
(`POST /api/orchestration/agreement/draft`, `"document": "new"` for the
starting point) is kept under `state/orchestration/`, **preview**
compares two resolutions and writes nothing, and **apply** puts the
previewed draft in force, refused when the draft or the agreement moved
since the page read it. There is no shadow stage: an applied agreement
whose Bench scope is enabled and grants an authority can authorize that
action.

**The agreement, v1.** A `bench` scope — `enabled`, `paused`,
`authority` (`readiness`, `reasoning`, `implement`, `review`, `repair`,
`recovery`, `integrate`, `merge`, each true or false, false when
unnamed), `capacityModel`, `capacity`, `scopeDefaults`, `exclusions`,
`handback` — and `phases`, keyed by phase card number, each with
`authority`, `limits`, `ceilings` and the same toggles (task 182; see
"Shared capacity, first come first served" below). The D7 defaults are
written into every new draft:
2 implementation slots, 1 review slot, 0 reasoning slots (reasoning and
recovery then take implementation slots — counted, never free),
20 launches a day, 6 launches per card, 2 repair rounds, 2 transient
retries, a 120-minute run-age threshold (a warning, never a kill) and
2 recovery attempts per incident (`recoveryAttempts`, task 144) and
0 setup retries (`setupRetries`, task 164: off until an operator sets
it, and an agreement written before it reads 0).

**Only narrowing, only two levels.** A phase's authority is the
intersection with the Bench scope's, every word carrying the scope that
decided it; a phase asking for more is refused (`authority-expansion`).
Ceilings may only be lowered (`ceiling-expansion`). Slots and daily
starts are the board's **shared** totals (below): every running agent —
a person's own launch included — counts against the scope that holds
its card and the board's total, and is never cancelled to make a limit
look met. A phase entry naming phases is refused (`nesting-refused`).
Any refusal turns every scope off.

**Shared capacity, first come first served (task 182).** Capacity is a
set of global pools, not slices handed to phases:

| key | what it is | example |
| --- | --- | --- |
| `bench.capacity.implementation` | the most implementation runs at once on the whole board — repairs included | 8 |
| `bench.capacity.review`, `.reasoning` | the same for independent reviews and for reasoning/diagnosis runs (0 reasoning: diagnoses take an implementation slot) | 2, 1 |
| `bench.capacity.dailyStarts` | launches a day on the whole board; a finished run refunds nothing | 40 |
| `bench.scopeDefaults` | the **default ceiling** every phase scope inherits: the most it may use at once, and launch in a day. A ceiling reserves nothing | `{"implementation": 2}` |
| `phases.<n>.limits` | one phase's own ceilings, replacing the default for that phase only; the global total always wins | `{"implementation": 1}` |

Standalone work has no partition of its own: the Bench scope's ceiling is
the global total. A slot is used only while a run holds it — a run, or
an operation whose outcome is not yet settled — so an idle, Backlog,
unadopted, paused or delivered phase uses none. **A worked example:**
with 8 implementation slots and a default phase ceiling of 2, a phase
with one member running uses 1 slot, not 2; the other 7 are open to any
eligible card, standalone or another phase's, each within its own
ceiling. If the phase starts a second member it uses 2, and a third
waits on its ceiling (`slots-full`, the scope's) while a standalone card
behind it in the queue still starts.

**Who is admitted, and when.** Every eligible launch request — new
implementation work, a review, a repair — waits in one durable queue
(`manager/local/state/orchestration/queue.json`) from the first pass that
finds it eligible but for capacity: authorized, its dependencies met, its
phase's strategy allowing it, nothing in the ledger holding it. Each
pool is admitted **first come, first served** across the Bench scope and
every phase scope, in the order the queue assigned each request when it
became ready — the queue's own count, never the wall clock, so a clock
stepped backwards moves nobody (requests that became ready in the same
pass are ordered by card number, file and action). Priority, kind (a repair is not
ahead of older new work) and the order scopes are read in decide
nothing, and a poll, a restart or a re-read never moves a request
forward. A request whose scope is at its ceiling, or whose declared
`**Touches:**` overlap running work, waits *without* stopping the ones
behind it — there is no head-of-line blocking. The queue on the page
shows each request's position, since when it has waited, and any hold.

| what happens to a request | its place |
| --- | --- |
| a temporary hold: its scope paused, a dependency that went back, a declared overlap, a transient backoff, the circuit breaker, its work base | kept, marked with the hold; it blocks nobody until the hold clears |
| admitted (its operation's intent written) | it leaves the queue |
| no longer wanted: its brief or head changed, its card left the stage or its scope, it was excluded or handed back, someone else moved it, the ledger settled it (ran, refused, unknown, retries spent), its card has had its `launchesPerCard` | it leaves; if it comes back it joins at the back |
| a card moves from implementation to review | the review is a new request (its head), queued when delivery says a review is next; a repair after it is another |

Dependencies and a phase's sequential order are unchanged: they decide
whether a request is eligible, and the queue decides only the order among
eligible ones. A recovery diagnosis or restart is admitted through the
same step and waits behind every older eligible request in its pool
(`queued-behind`); it is never queued ahead of them.

**Admission is one step.** The capacity reading and the intent that
reserves the slot are written under one lock (`orchestrator.admit`), so
the scheduler's launches, delivery's reviews and repairs and a recovery
restart can never each read the same free slot. One process executes at
all (the OS execution lock), and one request is one operation key, so it
never launches twice. A person's own ▸ start work and the legacy phase
runner's members are not orchestrated, are never refused for capacity and
are counted the moment they run.

**Release is what was observed.** A slot is freed when the run's ending
is observed, when a start is refused or provably ran nothing, or when a
person settles an unknown outcome. An intent with no receipt, an
`unknown` outcome and a run still alive after a restart keep their slot;
a handover is refused while any of them is open. Completion needs no
release step. Lowering a limit below what is running stops new admission
and stops nothing; each run keeps the grants frozen in its manifest.
Daily starts and launches per card are counted from the ledger and are
never refunded when a run ends.

**Migrating a reserved agreement.** An agreement written before task 182
names no `capacityModel` and is read as `reserved`, exactly as before:
each phase's `allocation` is set aside from the total and the Bench scope
keeps the rest — which is how a board with 8 slots, 7 of them allocated
to idle phases, left standalone work waiting on `slots-full (1 of 1)`.
Nothing migrates on a read, a restart, an install or an update. In
**Orchestrator → Operating agreement**, **Preview the migration** shows
each phase's old allocation and what it becomes, and per pool how many
requests wait, how many slots are in use and how many more standalone
work could start now and after migrating; **Migrate to shared
capacity** (`POST /api/orchestration/agreement/migrate`, `"apply": true`
with the revision the page read) writes the agreement once: the global
totals, every authority word, pause, exclusion, hand-back, strategy, note
and per-card ceiling stay exactly as they were; each `allocation` is
removed, and either the phase inherits `scopeDefaults` (the default) or,
with *keep each phase's old numbers*, its old values become its own
`limits` — ceilings that reserve nothing (a 0 stays a 0). Adoption,
cards, operations, the launch ledger and the history are not touched, so
usage and budgets carry over. A stale revision, or an agreement already
shared, is refused with nothing written. Afterwards a phase entry with
an `allocation` is refused (`allocation-retired`), and applying a draft
still written in the reserved model is refused
(`capacity-model-downgrade`), so an old page or file cannot bring
reservations back.

**One scope per card.** A standalone card is the Bench scope's; a phase
card and its members are their phase scope's when the agreement names
the phase **and** a person has adopted it (task 143, see "Phase scopes:
one scheduler per phase"). A phase the agreement does not name
(`legacy-phase`), or names but nobody adopted (`phase-not-adopted`), stays
with the existing phase runner and no orchestrator scope schedules it, so
two schedulers never share members. Backlog and Done are never managed.

**Pause, exclusion and hand-back** (`POST /api/orchestration/scope`) are
a person's direct changes under the same revision guard. A Bench pause
reaches every phase scope; a phase pause reaches that phase only; work
already running is observed, not cancelled. Hand-back gives a card to
manual work without touching its branch, worktree or run. Every action
is authorized again immediately before it happens — the agreement is
read from disk each time — which is a different fact from a run's
grants, frozen in its manifest at launch.

**Merge delegation is the user's choice (D4).** Granting `merge` (or
`integrate`) needs no new gate. A delegated merge waits for exactly the
requirements already configured: unmet refuses, running holds, and an
ungated rule allows it labelled *ungated — not verified*. Nothing here
activates a gate or grants an override.

**One execution installation (D13, D14).** Execution needs both a
portable **designation** naming this installation
(`<work root>/orchestration/designation.json`, committed like any board
edit) and the non-blocking **OS lock** on
`state/orchestration/execution.lock`, so a second process here is
refused and a replica only reads. Git is not a lock and no heartbeat is
read as authority: another installation's designation is never taken
over, however stale it looks. It moves by a **controlled handover**
(`POST /api/orchestration/installation`, `handover`: refused unless no
run is in flight) and an explicit **adoption** elsewhere (`designate`),
after which the cards that were working at that moment need
`adopt-card` before automation touches them; work committed to after
the adoption is the new owner's own and launches as usual. A former owner that is gone for good is a person's call: confirm
its processes stopped, then remove the designation by hand.

**Reading it is free.** `GET /api/orchestration` returns the effective
agreement and the draft side by side (revisions, problems, the preview's
changes), each scope's authority with provenance, its capacity (global
totals and the scope's ceiling, or a legacy allocation) and current
usage with the runs holding each slot, the admission queue, the
installation's role, and what each merge rule
would require. It names no path, scrubs free text, launches nothing and
writes nothing.

### Last mover, not last editor

An orchestrator acts only on a card whose **latest stage move by a
person** was made by this board's own identity (D1, task 138) —
`git config user.name` in the work-state repository, the identity every
claim and every sync attribution already uses. Not who wrote the card,
not who last edited it, not its assignee alone, not whose commit a pull
carried, and not the board's own automatic stage changes. Someone else
creates an idea in Backlog and you move it into To Do: it is yours to
orchestrate. A teammate moves a card into To Do and it syncs here: it is
theirs, and it reads that way on your board.

**Every board move says who made it.** A committed move ends its message
with trailers — the subject line is unchanged:

```text
board: 7 → to-do (ada)

Bench-Move: 1
Bench-Move-Id: m-4d494a3755fa
Bench-Mover: ada
Bench-Mover-Kind: person
```

`person` is a move made through the page (or a merge & clean up a person
clicked); `automated` is the board's own bookkeeping — an agent's run
moving its card to review or back, the phase runner, a launch walked
back, a phase's sweep. A move that was not committed (`BOARD_COMMIT_MOVES`
off, or a commit that failed) is one line in the machine-local journal
`manager/local/state/movements/journal.jsonl` instead, naming the
work-state head it was made on: a commit that head already contained
came before it, and one a later pull brought came after it and
outranks it. Trailers travel
with their commit through a push, a pull and sync's replay, so a move you
made on another machine reads as yours here.

**How a card's history reads** (`movements.eligibility`, the one
resolver):

| the record | reads as |
| --- | --- |
| trailers, kind `person`, the name equal to the commit's author | that person's move |
| trailers, kind `automated` | skipped: the delegation before it stands |
| trailers naming someone other than the commit's author | `ambiguous-move` (attribution conflict) |
| a `board: ` commit with no trailers, written before task 138 | `ambiguous-move` — it cannot say whether a person or an agent moved the card |
| any other commit renaming a card between stages (a person's `git mv`) | its author's move, as sync attributes an arrival |
| a merge commit | nothing: the commit it merged is the record |
| a card whose newest record is not where the card now is | `unrecorded-move` — moved by hand after the last record |
| a card created in its stage and never moved | `no-movement` — creating or editing is not a move |

Eligible is `own-move`; a phase member reads `phase-delegated`. Every
other answer is ineligible and says why, who and what to do:
`foreign-move` (another person moved it last), `held-by-other` (your
move, but someone else is its Assignee), `no-identity`,
`history-incomplete` (no person's move within the 500 commits read),
`history-unavailable`. The remedy is always an act a person can see —
move the card yourself, or take it over. Identities are compared
literally: no case folding, and a changed `user.name` is a different
person to this board until the card is moved again. **Attribution is
Git's**: a name in a commit, not cryptographic authentication.

**Content never moves ownership.** A comment, a refinement, a report, a
claim, the phase log, any edit by anyone — none of them is a stage move,
so none can take a card over or give it back. A later move by someone
else revokes eligibility before the next action; it is not undone and
work already running is not stopped. Backlog and Done stay out of every
scope, whoever moved the card there.

**Phases delegate, members stay their own.** A phase's members are
delegated by the phase card's own qualifying move, inside the phase
scope only — the Bench scope never acquires them, and a phase the
agreement does not name stays the phase runner's whatever its movement.
A member someone else holds (`member-held`) or whose own latest move was
someone else's or ambiguous (`member-unreconciled`) needs reconciling —
take it over, or move it yourself — before a child execution touches it.

**Reading is passive.** One `git log` of at most 500 commits over
`tasks/`, memoised by the repository's head, and the journal's newest
lines; nothing is fetched, written or launched. `orchestrator.authorize`
refuses with `not-eligible` and the whole answer, and the orchestration
reading carries the same answer per managed card (`cards[].eligibility`)
and this board's identity (`identity`), so the launch path and the page
cannot disagree.

### Orchestrator reasoning: launched agents, public responses

Every semantic judgement an orchestrator makes — is this card ready, what
must the owner decide, how should it be sequenced, what happened to a
stalled run — is a **launched coding-agent job** with the card's context
delivered to it (D2, task 139), never a bare model call inside a
scheduler or a renderer. `reasoning.py` owns the job; the scheduler that
decides *when* to ask is a later member (task 141).

**The `reason` intent** is the refine stance exactly — no worktree, no
command, no check, no edit tool, no shell, cwd the application checkout —
plus one requirement: its context must be *delivered*. Bench pins the
card (its brief and its public decision history), its phase card (or a
phase card's members), its dependency cards and a facts document of its
own public readings (the card's scope and the authority the agreement
gives it, its movement eligibility, the evidence bench recorded, the
input binding and the response contract) through `profiles.pin`, and the
adapter grants each pinned copy by one exact read. So an adapter must
declare `inputs.delivery: exact-read` as well as `read-only-tools` and
`shell-withheld`. Claude does; opencode does not, lists the intent under
`refusedIntents` with its reason, and every launch through it is refused
by name (`intent-unsupported`) — shown in **Settings → Agents** beside
the effective model, and in the Configuration check. A profile document
may add approved context to `reason`; `commands`, `verify` and `prepare`
are refused there (`intent-restricted`).

**Which model.** `BOARD_AGENT_MODEL_REASON`; empty, the review model
when reviews ride the same harness (D2: reasoning and review follow one
configured model), else `BOARD_AGENT_MODEL`, else the harness default.
The job records the model and the setting it came from, and the run's
manifest records the whole selection, like every launch.

**The response, version 1.** One JSON object between `<<<REASONING` and
`REASONING>>>` at the end of the run's output (or `REASONING DECLINED:
<reason>`):

```text
v, kind ("bench-reasoning-response"), operation, need, card, inputs  — identity and binding, echoed
summary                                                              — public, ≤ 1200 characters
findings   [{id, statement, severity, evidence}]                     — ≤ 20
questions  [{id, question, options, recommendation, why, evidence}]  — ≤ 10
actions    [{id, action, card, why, evidence}]                       — ≤ 10, proposals only
blockers   [{id, blocker, evidence}]                                 — ≤ 10
```

There is no field for internal reasoning, and an unknown key anywhere is
refused, so a chain-of-thought dump is refused rather than kept.
Evidence references are `input:<name>` (an input delivered to this run),
`card:<number>` (a card in its context) or `evidence:<id>` (a record in
its facts) — nothing else. An action names an orchestrator action word,
`ask-owner` or `hold`, and a card in the run's own scope (the card, or a
member of this phase).

**What bench does with it.** Validates bounds, identity (operation, need,
card), scope, references and the binding — the revisions the run was
launched with, and the board's revisions again when the run ends. A
response that fails reads `invalid`, one about a card that changed while
the run reasoned reads `stale`; both are kept as diagnostics with the raw
text bounded, scrubbed and marked inert, and neither is ever read as an
instruction. A `valid` response is a proposal: every action carries
`executable: false`, nothing here executes one, and anything that later
acts on it asks `orchestrator.authorize` again. A response cannot edit
the agreement, move a card, authorize a command, relax a merge rule or
answer the owner's question for them. A repair is a different, expressly
authorized `work` or `act-pr` launch through its own path.

**Asked on a need, once.** `reasoning.request(card, need)` is the only
thing that launches. A request is keyed by a fingerprint of the need and
the input binding: the same question about unchanged inputs — a repeated
scheduler tick, a double click, two concurrent callers — answers with the
job already there (`duplicate`); a changed card, phase, dependency or
agreement is a new question. A failed or cancelled attempt is asked again
only with `retry`, and one reasoning run per card is in flight at a time.
Opening a card, `/api/state`, `/api/orchestration` and `GET
/api/orchestration/reasoning` read records and launch nothing.

**Accounted, not hidden.** An orchestrator's request is authorized by
`orchestrator.authorize("reason", …)` — slots, daily starts, launches
per card — and appended to the launch ledger; a running reasoning run
occupies the reasoning pool (an implementation slot where none is
allocated), a person's own request included. Each job records its pool,
harness, model, policy, pinned bundle and inputs, and the usage the
vendor reported — `not reported` today, never zero.

**The record.** Machine-local under `manager/local/state/reasoning/`:
the job (status, binding, inputs, validated response or diagnostics,
usage, provenance) and the facts document each run read. A restart
closes a run in flight as failed and re-runs nothing. `reasoning.public`
is what the Orchestrator view reads: no path, no private reasoning, no
credential. That local record is not the one a person reads: when a job
settles, its public outcome goes onto its card (next section), and the
job's `cardRecord` says whether the card holds it.

**Orchestration prompts: adapting what reasoning is told** (task 153).
What a reasoning run reads is two managed templates, composed, and an
operator adapts them in **Settings → Agents → Orchestration prompts**
the way the launch templates are adapted (see "Agents: harness, model
and prompt per purpose"):

| template | used by | edit it to |
| --- | --- | --- |
| `reason.md`, shared | every reasoning run — all four needs, the Bench scope and every phase scope | change the framing, tone and house rules every reasoning run is given |
| `reason-readiness.md` | a readiness request | change what "ready" means here |
| `reason-questions.md` | a questions request; what it drafts lands on the card for the owner | change what to ask the product owner and how |
| `reason-plan.md` | a plan request | change how delivery is sequenced |
| `reason-recovery.md` | each recovery diagnosis attempt (see "Recovery: diagnose, don't kill") and a person's recovery request | change what a stalled or failed run's diagnosis looks at |

The need's guidance is rendered first — it may name `{card}`, `{stage}`
and `{filename}` and nothing else — and then inserted into the shared
template as `{question}`. The shared template may use `{operation}`,
`{need}`, `{question}`, `{filename}`, `{stage}`, `{card}`, `{inputs}`,
`{contract}`, `{guidance_read_only}` and `{context}`, and must keep all
but `{filename}` and `{stage}`, together with the `<<<REASONING`,
`REASONING>>>` and `REASONING DECLINED:` instructions the board parses.
A literal brace is written doubled. Every value is inserted as data:
a `{branch}` in a card, a facts document or the agreement reaches the run
as those characters, never as a placeholder.

- **When it applies.** Templates are read at every reasoning request.
  A saved change reaches the next run launched; a run already started
  keeps the prompt it was given. The same question about unchanged
  inputs is still answered by its earlier job (`duplicate`), so an
  edit reaches the next new question, a deliberate `retry`, or a
  recovery attempt — not an answer already given.
- **What a run records.** The job's `prompt` and its manifest's
  `template` name the shared template (name, source — `built-in` or
  `local` — and sha256), the guidance template the same way, and, on the
  job, the sha256 of the composed text — never the text. An earlier run's
  record keeps the identity it was launched with.
- **What a prompt cannot do.** It is instruction, not authority. The
  response schema and its bounds, the evidence-reference rules, the
  scope check on proposed actions, `orchestrator.authorize`, the reason
  intent's grants (no shell, no edit tool, no worktree, exact reads),
  the operating agreement, budgets, ownership and lifecycle are all
  enforced in code whatever the prompt says, and reading, previewing,
  saving or resetting one launches no agent and no check, moves no card
  and changes no agreement.
- **Troubleshooting.** A template that breaks its contract is refused
  before saving, with the code and the field (`template-unknown-field`,
  `template-missing-field`, `template-missing-marker`,
  `template-malformed`, `template-too-large`, …). A local override that
  already breaks it — edited by hand, say — is shown with its problems in
  Settings and refuses every reasoning request that would use it, before
  a job, a manifest or a process exists, with *fix or reset it in
  Settings → Agents*; it is never repaired or silently replaced by the
  built-in. **Reset to built-in** removes the override, and the replaced
  text stays under **Recover replaced text**. Overrides live in
  `manager/local/prompts/`, which updates never replace, so they survive
  an update and a restart; a built-in that changes in an update reaches
  you only where you have no override, and **Diff from built-in** shows
  how yours differs.

### The card keeps everything: decisions and public history

An orchestrator's questions to the product owner, the owner's answers,
the reasoning responses, blockers, recovery attempts and actions live
**on the card they are about** (D11, task 140) — never only in a
machine-local ledger. So a board that lost its local state, a restarted
board and a replica a sync reached all read the same open questions and
answers. The board is the only communication surface (D12): nothing here
notifies anyone, sends a message or listens for a reply.

**Where.** A history section, `## Orchestration record`, in the card's
history half, so a refinement never touches it (a proposed brief that
contains the heading is refused). One record per entry: a
`<!-- bench:orchestration {…} -->` line, `"v": 1`, and under it one
readable line the board writes from the record and never reads back
(`- 2026-11-18 10:02 UTC · Question q-… (agent-reported) — …`). Records
are appended, never edited; the newest about a question is its state.

| kind | holds | source |
| --- | --- | --- |
| `question` | the question, its options, recommendation, why, evidence references (`input:`, `card:`, `evidence:`), the brief revision it was asked against, its owner, the operation and need that drafted it | `agent-reported`, or `person` |
| `answer` | the option chosen and/or a note, the actor, the question's newest record it answered (`basis`), the brief revision, the scope | `person` |
| `resolution` | `withdrawn` by a person, or `superseded` by a newer response to the same need — the question's text and options stay | `person` / `observed` |
| `response` | a reasoning run's validated public response (summary, findings, proposed actions — `executable: false` — blockers, the questions it opened), or a refusal's codes, or why it ended | `agent-reported` |
| `blocker`, `attempt`, `action` | a blocker opened or cleared; one recovery attempt (`n of m`) and its outcome; an action's intent, then its observed receipt | `observed` or `agent-reported` |

**Who writes.** A settled reasoning job (`reasoning.on_card`) writes its
`response`; a valid one opens its questions, addressed to the person
whose board asked, and supersedes the open questions an earlier response
to the same need opened. A person answers or withdraws
(`POST /api/card/decisions/answer`, `/withdraw`). The engine's later
members record facts through `decisions.note`, with an idempotency key so
a restart replaying a receipt writes it once. Each write is one
`board: <n> …` commit under `BOARD_COMMIT_MOVES`, synced like any board
edit.

**An answer is bound to what the person saw.** The request names the
question, its newest record (`basis`), the brief's revision, the identity
the page showed (`actor`) and a request identity. The actor is this
board's git identity, never a body field. Refused, nothing written: a
newer record about the question (`409 stale`), a brief edited since the
page read it, a page showing another identity, a question already
answered, withdrawn or superseded, and a question asked against an older
brief — it needs re-evaluation first, and reads `needsReevaluation`. A
question addressed to someone else is `403 refused`: readable, not
answerable from this board. The same request identity again returns the
first record (`duplicate`), and of concurrent answers exactly one is
recorded.

**A phase aggregates; members own.** A member's question is on the
member card. The phase card's reading (`aggregated`) lists its members'
questions, each with its origin (file, stage, number); the answer is
posted to — and stored on — the member, with its actor and revisions.
Nothing is copied onto the phase card.

**Card text is data.** Records are read only from that section of the
history; one anywhere else (a brief, a quoted report) is counted
`misplaced` and ignored. A line that does not parse, an unknown kind, a
wrong version or a missing identity is counted `malformed` and skipped.
Each kind is read field by field, so an unknown key — `reasoning`, say —
never reaches a reader; strings are stripped of control and
bidirectional characters and credential-shaped text is masked on the way
in *and* out; every angle bracket in a record line is escaped and the
readable line keeps none, starting with `- <date>` so no reader takes
it for a header, heading, marker or criterion. Nothing read here grants,
launches, moves or merges anything. Masking is pattern matching, not a
promise that every secret is found; the raw text of a refused response,
transcripts and approved inputs' contents never go onto a card.

**Bounded.** Text fields are capped (2,000 characters; 6 options; 8
references; 20 items), one record at 32 KiB and a card's section at
1 MiB. Over any of them the write is refused with nothing written and
the card's other history intact; the reasoning job keeps its response
locally and its `cardRecord` names the refusal.

**Revisions.** The reasoning binding's card revision
(`decisions.card_rev`) is the card's text without the records bench
itself wrote, plus every decision a person made: putting a response on
the card does not make it stale, and an owner's answer — like a brief
or hand edit — is a new input, so the same need is a new question.

**Reading is free.** `GET /api/card/decisions` (the whole reading:
questions open and resolved with their history, responses, blockers,
attempts, actions, the counts and the sources' meanings) and
`/api/state`'s `decisions` (per card: open, needing re-evaluation,
answered, open blockers, responses — absent on a card with no such
history) read card text and write, launch and send nothing. There is no
index to lose: the reading is the card.

**What is proved where.** `tests/test_reasoning.py` launches through the
Claude adapter's real `run` against a stub vendor that answers from the
files it was granted, and holds every refusal above. Whether the
*installed* CLI honours the exact-read grants with a real model is the
opt-in, billable `develop/reasoning-probe.py --approve-real-agent`,
which nothing runs by itself.

### Scheduling: bounded passes, durable operations

An enabled agreement is launch-capable (D16, task 141). Every
`BOARD_ORCHESTRATE_INTERVAL` seconds (30 by default) the designated
installation runs one **pass** (`scheduler.py`): observe, reconcile,
plan, execute. With no agreement, an invalid one, or a Bench scope that
is not enabled, a pass reads the agreement and returns; a replica, or a
board that is not designated, returns before observing anything. The
designated installation takes its own execution lock on its first pass
after a restart, and never another's. The first pass waits one interval,
so starting a board never launches anything in the moment it comes up.

**What is launched, and how.** Today the scheduler launches
implementation work — the `implement` authority — and nothing else; the
review, repair, recovery and phase-integration steps are later members
of phase 136. A card is a candidate when it sits in To Do or In Progress
under a scope that manages it, has no run, is not a phase card, and
nothing in the ledger holds it. A To Do card is moved into In Progress by
the board (an automated move, so the person's delegation stands, D1) and
then started through **▸ start work**'s own path, `agents.start_agent`,
with every preflight it has — plan, profile, required inputs, template
and claim. There is no second way to cut a branch or make a worktree. A
launch the board's own move preceded and that is then refused walks the
card back to To Do.

**Who goes first.** Eligibility first: a card whose `**Depends on:**`
names unfinished work waits, a card on a dependency cycle is blocked by
name, a number not on the board is said so, and a phase member waits for
its phase's strategy. Among eligible requests — implementation work,
reviews and repairs alike — first come, first served per pool, across
every scope (task 182; see "Shared capacity, first come first served").
Priority does not reorder the queue. A phase's members are only ever its
phase scope's; the Bench scope never schedules them, and a phase the
agreement does not name stays the legacy runner's.

**Declared overlap (D10).** An optional `**Touches:**` header lists what
a card's work changes — `**Touches:** src/api, docs/guide.md` — compared
by path segment (`src/api` overlaps `src/api/users.py`, not
`src/apis`). A card whose declaration overlaps running or already chosen
work waits (`touches-overlap`). A card that declares nothing has
*unknown* overlap: it is not blocked, and the schedule says `overlap:
unknown` beside it with the cards it is unknown against — never safe.
Nothing is inferred from a brief's prose.

**Capacity is reserved before the launch.** The authorization is asked
first; then the **intent** is written durably to the operation ledger
(`manager/local/state/orchestration/operations/<key>.json`), which
occupies the slot and spends the start; then the side effect; then the
**receipt**. `orchestrator.authorize` counts a recorded intent, an
unknown outcome and a run still alive after a restart exactly as it
counts a running agent — a person's own run included, which is counted
and never cancelled. Daily starts and launches per card are counted from
the ledger, so a restart, a pause and resume, or an agreement edit
resets nothing; a phase's starts count against its own daily ceiling
and the board's total alike.

**Re-checked immediately before every side effect.** The agreement, the
card's last mover and holder, exclusions, hand-back and pauses (a Bench
pause reaching every phase scope) are read again before the move and
again before the launch, each time from disk. A person's move or
takeover between the two wins: the launch is refused and recorded as
such. A pause starts nothing new and observes what is already running.

**An operation's key is the decision.** `op-…` is a digest of the
action, the card, the brief's revision and the attempt, so the same
decision taken twice — a second pass, a restart, two callers — is one
record, and the second is answered as a duplicate. A report, a review
or a receipt appended to the card is history, not the brief: it never
makes the same brief a new question.

**When a pass dies half-way.** The next pass reconciles every intent
that has no receipt against what bench can observe:

| found | settled as |
| --- | --- |
| a run on the card started after the intent | `launched`, the run is its receipt |
| no plan recorded since the intent | `abandoned` — start work records its plan before any process starts, so nothing ran; it counts as a transient retry |
| a plan recorded, no run tracked | `unknown` — held, the slot kept, the card not launched again |
| a launched run whose board restarted, its process still alive | `unobserved` — it keeps its slot |
| the same, its process gone | `ended`, outcome `unobserved` |

A launch that raised something unexplained is `unknown` too. Nothing is
retried blindly and nothing claims exactly-once from a local file: an
unknown outcome is a person's to settle with `POST
/api/orchestration/operation` (`{"operation", "decision": "launched" |
"not-launched"}`), and a refused launch may be `release`d to be tried
again on the same brief. Settling launches nothing; a handover is
refused while any operation is unsettled.

**Failures, classified (D9).** A refusal before anything happened — a
preflight said no — is `refused` and not retried until the brief
changes or a person releases it. A launch whose message marks it as
transient (a process that could not start, a wait that timed out) is
`retry`, and a run that *ended* failed is classified from its log's
last words when its ending is observed (task 144): `transient` (a
transport or setup failure — a timeout, an overload, a dropped
connection, an HTTP 429 or 5xx), `denied` (refused something it needed),
`deterministic` (an assertion, a traceback, a failing test) or
`unknown` — and unknown is never presumed transient. Only transient
failures are tried again: up to the scope's `transientRetries` ceiling,
counted together, with exponential backoff from 60 seconds. Every other
run that ended — finished, failed, declined, held — is never relaunched
by a pass on the same brief; a failure that is not transient, or whose
retries are spent, becomes a recovery incident (next section).

The **circuit breaker** is durable (`state/orchestration/breaker.json`):
three failures across the board — launches that could not start, runs
that ended failed, incidents exhausted — within thirty minutes and with
no run finishing between them open it for ten minutes, and a restart
does not close it. While it is open no launch, diagnosis or restart
starts; endings are still observed. `GET /api/orchestration` names the
cards behind it and the owner's choices, and `POST
/api/orchestration/recovery` with `{"action": "reset-breaker"}` is a
person closing it once the cause is fixed.

**On the card, and on the page.** Each operation's intent and receipt
are `action` records in the card's `## Orchestration record` (see the
section above), keyed by the operation so a replay writes once. `GET
/api/orchestration` carries `schedule`: every candidate with its state
(`ready`, `chosen`, `waiting`, `held`, `blocked`, `working`,
`unmanaged`), its code and its reason, the circuit breaker, the last
pass and the ledger's counts — read from files, with nothing reserved,
acquired, written or launched.

### Choosing a work base: main, or committed work in Review

Before the scheduler launches implementation work, it decides which code
the work starts from (task 150, `basechoice.py`), writes that decision
onto the card, and the execution plan carries it through the branch,
the worktree and the pull request. It is a decision bench makes and
checks. The coding agent is never asked to find the right base after
its checkout already exists.

**The example.** Card A adds an API and is in Review: its branch
`task/A` holds the committed API, and `main` does not have it yet. Card
B builds a UI on that API and says so: `**Depends on:** A`. Card C has
nothing to do with either.

- **B** builds on A's committed work. The orchestrator records *Building
  on #A's committed work*, pinned to the exact commit `task/A` held. B's
  branch is cut from that commit, so its checkout contains A's API. B's
  PR targets `task/A`, so its review shows B's changes and not A's again.
  `main` is kept on the record as the normal base, for when A lands.
  Nothing of A's is merged to make this happen.
- **C** starts from `main`, recorded as *Starting from main*.
- Before this change, B waited until A was done. A dependency in Review
  is now something the decision weighs, not a wait.

**What counts as evidence, and what does not.** Only a dependency the
card declares in `**Depends on:**` can be selected. Being in Review, a
similar title or touching the same files is never enough on its own.
Such cards are listed under *Not selected*, with the reason. Only
committed work counts: the prerequisite's branch must exist here and
hold commits the normal base lacks, and uncommitted edits in its
worktree are invisible to the decision. A dependency already in `done/`
is part of the normal base. A dependency still being worked (backlog,
to-do, in-progress) holds the card as before. There is no relevance
score.

| decision | when | what a new start does |
| --- | --- | --- |
| `normal` | no declared dependency has committed work in Review | starts from the configured base (`main`) |
| `stacked` | exactly one has, or several form one chain | starts from that pinned commit, PR into its branch |
| `explicit` | the card has its own `**Base branch:**` header | the header governs. The decision never overrides it |
| `integration` | a phase member | its phase branch governs |
| `blocked` | the prerequisite's branch is missing, holds no committed work, makes a cycle, or there are several unrelated prerequisites, or a phase member needs a Review card outside its phase | nothing starts. The card says why and gives the remedy |
| `stale` | the parent changed after the decision | a new start is refused until the next pass decides again |

**Automatic, and where the owner decides.** Inside an enabled operating
agreement with the `implement` authority, the ordinary choice is made
automatically, with no question asked. Bench does not resolve the cases
it cannot decide safely:

- Several unrelated prerequisites are never combined, cherry-picked or
  put on a new integration branch. The card is blocked until a person
  picks an option: wait for all but one to land, stack the
  prerequisites on each other, or write a `**Base branch:**` header.
- A phase member never leaves its phase branch. A prerequisite outside
  the phase is a conflict. The fix is a person's: add the prerequisite
  to the phase, wait for it to be delivered, or remove the dependency.
- An explicit header always wins. If a declared prerequisite is not on
  the header's branch, the record says so and the header still governs.

**What the card records.** A `base` record goes into the card's `##
Orchestration record` (see "The card keeps everything"). It holds the
decision and its code, the branch and the exact commit, the normal
base, the intended PR target, the prerequisite card, its head and its
PR, a fingerprint of the inputs, the rationale, and the alternatives
not selected. The record is portable, so a sync, a restart and a replica
all read the same decision. Writing it is idempotent: the same inputs
find the record already there, so a repeated tick writes nothing new.
The card's Overview shows the record as a **Work base** block under
**Where it stands · what happens next**: the plain headline and why,
with the refs, the evidence and the history folded under it. Reading the board, the card or the schedule never fetches,
decides or writes. The schedule's assessment reads local refs only.

**Held to it before launch.** The plan reads the decision as a fourth
input, after phase membership and the card's headers and before the
application default (see "Execution plans"). A new start must begin from
the pinned commit itself. If the prerequisite's branch moved after the
decision, the preflight refuses before any branch, worktree or run
exists. The scheduler also checks again immediately before the launch,
through the launch's own branch resolver, fetching as the launch will.
When it finds the branch moved, it records the decision `stale` on the
card. That attempt counts as one that ran nothing, and the next pass
decides again on the new commit. The decision, its freshness and the
launch all read a branch the same way: the remote's copy as last
fetched, else this checkout's. So a prerequisite whose local and remote
copies differ cannot be pinned by one reading and refused by the other.
A decision the board could not write onto the card (its record section
full, say) launches nothing, because the plan could not read it and the
work would start from the application base instead.

A stale decision says who decides it again. While an orchestrator
manages the card, its next pass does. On a card worked by hand nothing
does, and ▸ start work's refusal says so and names the
`**Base branch:**` header as the way through. A card whose work is
under way reads *stale; the work under way keeps its recorded plan*,
never that something is reassessing it.

Once the work is under way (its task branch exists), resuming follows
its recorded plan, exactly as before. A retry, a resume or a restart
never makes the decision again. It does not re-pin a moved parent, and
it does not rewrite the decision to `normal` once the parent landed.

A blocked decision is written onto its card by the scheduler's pass,
with an open blocker naming the conflict and the remedy, on any card the
scope may act on. The next decision that is not blocked clears the
blocker. No reasoning run is launched for it. The blocker is the owner's
prompt, and the remedy is a person's choice.

**As the prerequisite evolves.** Every pass checks each card whose
decision stacked it on another. When the parent has moved, gone back to
an earlier stage (its review rejected it), landed in `done/`, lost its
branch or left the board, bench writes one `stale` record naming the
change and the remedy. For work not yet started, the next pass decides
again. Work already under way is never changed under its worker. Its
next steps are the confirmed stack actions, each a separate, armed
click (see "Maintaining a stack: three confirmed actions"):

- **Update from** the parent: an additive merge that brings in the
  parent's new commits.
- **Retarget onto** the normal base once the host shows the parent
  merged, squash and rebase merges included. Bench reads the result back
  from the host. When the host cannot do it, bench names the manual step.
- **Finish: clean up**.

A retarget stops counting reviews and approvals given before it, so no
old approval carries over to the changed change. Bench never rebases or
force-pushes, and the orchestrator does not retarget by itself.

### Recovery: diagnose, don't kill

A run that has gone quiet is not a run that has stopped working, and a
run that died is not always one to start again (D5, task 144,
`recovery.py`). The last step of every pass looks at the work that is
not moving — and nothing in it stops a run for being silent.

**Five signals, never one.** For each running implementation run on a
managed card bench reads, separately: the run's last public **progress
update**, its last **tool activity**, whether a **tool call is in
flight** right now (a long test through the agent's shell is quiet and
busy — the event bridge's start of a tool call is the signal, and only
that call's own finish clears it, so a quick call finishing beside it
does not; the session going idle or ending closes them all), whether **bench is running a check** for the card, and its
worktree's **last commit** — beside its **process** (alive or gone, from
the process handle, not the registry's word) and its **age**.

| reading | when | what happens |
| --- | --- | --- |
| `young` | under the scope's `runAgeMinutes` (120 by default) | nothing |
| `active` | past it, and a tool event, progress update or commit within 15 minutes | a warning on the page; nothing else |
| `quiet-active` | past it, with a tool call in flight or a check running | a warning; nothing else |
| `stalled` | past it, process alive, and none of the above for 15 minutes | an incident, if an orchestrator launched the run |
| `ended` | the process is gone | the scheduler observes the ending |

A person's own run is observed and never diagnosed or stopped.

**An incident, and its bounded attempts.** A stalled run an orchestrator
launched opens one **incident**; so does the card's newest
implementation run ending in a failure that is `deterministic` or
`unknown`, or `transient` with its retries spent. A `denied` failure
opens one already held: recovery never works round a refusal. The
incident is keyed by the run, so a second tick or a restart finds the
one already open, and it is recorded under the tool's state and on the
card (a blocker, every attempt, every action). Each **attempt** is one
read-only diagnosis — a `reasoning` job with the `recovery` need,
authorized and counted as the `recover` action, so the agreement's
`recovery` word delegates it and it takes a reasoning slot (an
implementation slot where none is allocated), a daily start and one of
the card's launches like any launch. Its facts carry the incident: the
signals, the failure's class and headline, the attempts before it, and
the one remediation bench can carry out. The attempt's intent is written
before the request; one found after a restart without a job provably ran
nothing (a request records its job before any process starts) and is not
counted. At most `recoveryAttempts` attempts launch per incident — two by
default, a phase may lower it, a person may change it in the agreement —
with backoff between them; when they are spent the incident is
**exhausted**, held on the card with what was tried and the owner's
choices, and nothing tries again.

**A diagnosis proposes; bench observes.** The recovery run has no edit
tool, no shell and no worktree, so it cannot compete with an active
worker's edits. Its response lands on the card as any reasoning
response does. The one remediation bench carries out is a coordinated
**restart**, and only when the response proposes `recover` for this
card:

1. `recover` is authorized again, the card has no operation of unknown
   outcome, it is in `in-progress/`, and no other run works in it;
2. the stalled worker, if alive, is stopped through **‖ hold** — and
   the restart waits until its process is *observed* gone (two minutes,
   then the incident is held as `stop-unconfirmed`);
3. `implement` is authorized again and the restart is an operation in
   the ledger (intent, then receipt), launched through **▸ start work**'s
   own path and preflights, continuing the same branch and worktree —
   every commit and every uncommitted file kept. Nothing is reset,
   rebased or discarded, and no grant or check changes.

The incident is resolved only by what bench observes afterwards: the
restarted run's own tool activity, a check running for it, or the card
moving to `review/` — or, for a stalled run, the run becoming active
again or ending by itself. What the diagnosis *says* resolves nothing.
Any other proposal (`hold`, `ask-owner`, a merge, a review) holds the
incident as `diagnosed` for a person. A restart that fails closes its
incident; the new failure is an incident of its own, and the card's
launch ceiling bounds the chain.

**Held is visible.** Every held or exhausted incident is an open blocker
on its card, naming the code, the attempts launched against the cap and
the choices: fix the card and move it yourself, hand it back to manual
work, **‖ hold** the run, raise `recoveryAttempts`, or mark the incident
resolved (`POST /api/orchestration/recovery`, `{"action": "resolve",
"incident": …}`), which launches, stops and retries nothing. `GET
/api/orchestration`'s `schedule.recovery` lists every observed run with
its signals and reading, every incident with its attempts, the held
ones with their choices, and the breaker — a read: it launches, stops
and writes nothing.

### Delivery: checks, independent review, repair and your merge policy

Once a standalone card's work is in `review/`, the same pass carries it
the rest of the way (task 142, `coordinate.py`): the candidate's required
checks, an independent review of that exact head, a bounded repair when
either says no, and — only where the agreement delegates `merge` — the
merge, under the merge rules you configured. Each step is an existing
authority's own door: **Run required checks** (`required.start`, under a
person's work reading, `trigger: orchestrator`), **◔ review PR**
(`agents.start_pr_review`), **↻ act on PR** (`agents.start_pr_fix`) and
**merge & clean up** (`completion.complete_task`). In an adopted phase
scope the same steps carry a member to *integration* into its phase
branch and the phase card to delivery of its integrated head (task 143,
"Phase scopes: one scheduler per phase"); a phase the runner owns is not
touched here.

| authority word | what it lets the pass do |
| --- | --- |
| `review` | ask bench for the candidate's required checks (the `verify` action: no slot, no start) and launch an independent review (a review slot) |
| `repair` | launch ↻ act on PR after a failed check or a request for changes, up to `repairRounds` per card (an implementation slot) |
| `merge` | merge & clean up, waiting for exactly the requirements you configured |

**One head decides.** Every step is read against the commit
`gates.candidate` names now, and every operation is keyed by it. A new
commit makes earlier checks history (the gate's relevance rule), an
earlier verdict another commit's, and earlier operations about something
else: a moved head is asked about afresh, never answered by what was
selected for the old one. A merge takes exactly that commit —
`complete_task(expect_head=…)` pins it on an ungated board too, and a
branch that moved refuses with nothing merged.

**Independent means a different model, shown to be one (D3).** Before a
review launches, and again immediately before, the reviewer's resolved
identity — the review purpose's harness and model — is compared with
every implementer's: each run on the card that rode `work` or `act-pr`,
from its frozen manifest, and what those two purposes resolve to now (a
repair is an `act-pr` run). Names are compared through what the harness
declares in its `capabilities.json` `models` block — its full names and
the aliases its CLI resolves — so `opus` and `claude-opus-5-5` are one
model. Refused, with a remedy and nothing launched:

| code | what it means | fix |
| --- | --- | --- |
| `same-model` | the reviewer would ride an implementer's model, under any name | set `BOARD_AGENT_MODEL_REVIEW` to another model |
| `reviewer-model-unresolved` | no review model is set, so the harness picks one bench cannot name | name the review model |
| `implementer-model-unknown` | an implementer rode the harness default | name the work and PR-fix models; review that run yourself |
| `model-identity-unverified` | a name the harness does not declare | use a declared name, or declare it in a local adapter override |
| `review-unavailable` | the review harness is not installed or does not run reviews | choose another harness in Settings → Agents |

A verdict counts toward this step only when the run that gave it is shown
independent the same way, from its own manifest; one that is not is
shown (`counted`) and a new review is asked for. A request for changes is
acted on whoever gave it. A different model alone proves nothing about
the evidence: the review is a separate read-only run in its own pinned
checkout, and checks are bench's records at the head — never a report.

**Your merge policy, as you set it (D4).** A delegated merge waits for
the reading `orchestrator.authorize` takes of `gates.evaluate`: unmet
blocks (`requirements-unmet`), running holds, and an ungated rule merges
labelled *ungated — nothing is required by your policy, so this merge is
not verified*. With no rule, the card says **No checks required by your
policy**; no check is run, none is called passed, no gate is turned on and
no override is granted. Optional checks are never run here. Without the
`merge` word the card reads `merge-not-delegated`: ready, and yours.

**Distinct endings, each on the card.** Every blocked step writes a
`blocker` record on the card once per head (cleared when the card moves
on), and every step's intent and receipt are `action` records:

| code | what happened |
| --- | --- |
| `repair-exhausted` | the card has spent its `repairRounds`; an `attempt` record says *exhausted* |
| `repair-no-change` | a repair ended without a new commit |
| `check-not-granted` | a required check a person's work reading may not ask for — the permission is not widened |
| `verification-setup-failed` | a required check could not reach a verdict (preparation failed, refused, no verdict): not repaired. The reason says what failed, the setup retries used against `setupRetries`, why it is held, and the way through (fix the setup, then ↻ in the card's ◫ sheet) |
| `retry-setup` | the newest answer of every unmet required check at this head is a setup failure (preparation failed), and `setupRetries` is not spent: the same checks are asked again at the same head (see "Bounded setup retries") |
| `checks-unanswered` | checks asked at this head ended without answering; asking again is yours |
| `authority-revoked` | the agreement, a pause, an exclusion or another person's move withdrew the step between the decision and the act |
| `review-not-independent` | as above |
| `step-refused` | a check batch, review or repair launch was refused at this head (its preflight said no); it is not asked again until the head moves or a person releases the operation |
| `requirements-unmet`, `merge-refused`, `not-published`, `partial` | the merge rules, `complete_task` or publication said no |

**Bounded setup retries (task 164).** A required check whose newest
answer at the head is *preparation failed* — a required step that would
not complete, or a verifier's own report that its setup failed — is a
fact about the environment, not the code, so it is never repaired. With
`setupRetries` set above 0 in the operating agreement (Orchestrator →
Operating agreement → Ceilings; a phase may only lower it), delivery asks
for exactly those checks again, at the same head, through the same
**Run required checks** batch. The operation and each batch item record
the evidence id the retry answers (`retryOf`); `verify.request` itself
is asked exactly as a click asks. Every retry is a `verify` operation in
the ledger marked `retry`, counted per card and head from the ledger
itself — so a
restart, a pause or an agreement edit resets nothing, an intent that
provably ran nothing (`abandoned`) is not counted, an `unknown` outcome
holds the card before anything is asked again, and two callers with one
decision run it once. Nothing else is asked again: an assertion failure
goes to repair as before, and a denial, a timeout, a verifier's refusal
and a missing or broken report hold the card at once. A retry whose
batch ended without a newer answer is not repeated, and neither is one
the batch refused about the same failure — most often a check a person's
work reading may not ask for (one only the review profile is offered):
the card is held after that one refused operation, naming the work
profile's `verify` list as the remedy. More than 32 setup failures at
once are held rather than retried, since the ledger records at most 32
per retry. When the ceiling is
spent the card is held with an `attempt` record marked *exhausted* and a
blocker naming the check, the reason, `n of m` retries and the way
through. A retry takes no slot and spends no launch; it widens no grant,
starts no service, stops no worker and changes no merge rule.

What is *not* established, and stays unresolved: whether concurrent
browser runs on one host contend for a shared resource. #158's crashes
were never reproduced under controlled concurrency, so bench imposes no
installation-wide serialization of checks; a retry runs exactly as any
other request does.

**Uncertain merges reconcile, never repeat.** A merge's intent is written
before it. An intent found without a receipt is settled from the
delivered record (merged), the completion's recovery record (merged, not
published — its own retry publishes without merging again), then the
application remote itself: a head the remote's target already holds is
finished by `completion.complete_delivered` — cleanup and the move, no
merge — and a merge the next pass finds already landed is reconciled the
same way. A local merge that neither the base here nor the remote holds
provably did not happen. Anything else is held `unknown` for a person's
`POST /api/orchestration/operation`. Nothing here releases, deploys or
promotes anything.

`GET /api/orchestration`'s `schedule.delivery` lists every review card
with its state, code, reason, head, check words, review independence and
merge label — local records and local git only; it asks no remote and
launches nothing.

### Phase scopes: one scheduler per phase

A phase can be run by the phase runner (see "A phase runs itself, on a
branch of its own") or by its own **phase scope** — the same orchestration
engine as the Bench scope, narrowed by the phase's entry in the agreement
(task 143, `phasescope.py`). Never by both: every phase has exactly one
scheduling owner, and which one is written on the phase card itself.

**Adoption is explicit, and waits for quiet.** Naming a phase in the
agreement does not take it from the runner. A person adopts it (`POST
/api/orchestration/phase`, `{"card", "action": "adopt"}`), and adoption is
refused, naming what is still working, while the runner has a run in
force (‖ hold it first), an agent runs on the phase card or a member, or
an operation on any of them has no receipt. It also needs the agreement
to name the phase and this installation to be the one that executes.
Adoption writes one line to the phase log — `scope adopted by <name> —
…` — and changes nothing else: the phase branch, its worktree, the
members' branches, the log and every member the runner already
integrated stay exactly as they were, and an integrated member is never
run again. The runner reads that line where it reads everything else and
stands down (its state reads `scoped`): its beat advances nothing, **▸ run
phase** is refused, and its **‖ hold** is refused with the two acts it
used to combine named apart. `"action": "release"`, under the same
quiescence, hands the phase back; the runner does not resume until a
person runs it. A phase that is never adopted runs exactly as it always
did — which is also why the phase building this feature is not migrated
mid-flight: nothing adopts a phase by itself.

**What the phase entry says.** Beside `enabled`, `paused`, `authority`,
`limits` (its own ceilings at once and per day, which reserve nothing;
`allocation` in a legacy reserved agreement), `ceilings`, `exclusions`
and `handback` (authority and ceilings only narrowing the Bench scope's),
a phase names its `strategy`:

| strategy | which member may start |
| --- | --- |
| `sequential` (default) | the runner's rule: one at a time, in the order `## Cards` lists them, each after the one before it is **integrated** into the phase branch |
| `dependencies` | any member whose in-phase `**Depends on:**` are integrated, side by side up to the phase's ceiling and the board's free slots |

Membership stays the phase card's `## Cards` list, and members still
inherit the phase branch: no member names a base of its own. A member in
Backlog is never pulled out of it — a sequential phase waits on it, and
says so. A member someone else holds, or whose own latest move was
another person's, is `not-eligible` with the remedy until it is taken
over or moved (see "Last mover, not last editor").

**How a pass runs a scoped phase.** Each step has an intent and a receipt
in the operation ledger and its record on the card it is about:

1. **Prepare** — a phase card the owner moved into To Do is put
   in-progress (an automated move; the delegation stands) and its branch
   cut through the runner's own `ensure_branch`; main is merged into it on
   the beat while no member runs, a conflict a blocker on the phase card.
2. **Start members** — the scheduler, through ▸ start work's own path,
   under the strategy and the phase's own slots and starts.
3. **Integrate** — a member in review/ gets its required *member* checks
   and, where the phase delegates `review`, an independent review, then
   is merged into the phase branch through the runner's own merge (the
   member gate read again, the validated commit merged, never a rebase).
   Exactly the head that was checked and reviewed is merged — under an
   ungated member rule too — and a member branch that moved on since is
   refused with nothing merged; the receipt names the commit that landed.
   Delegated by `integrate`; without it the card reads
   `integrate-not-delegated`. A member that is integrated reads
   `integrated` — **in the phase branch, not delivered**: it stays in
   review/ and is not in the application base.
4. **Finish** — once every member is integrated: the branch published,
   the phase card into review/ and its PR opened, as the runner does.
5. **Deliver** — the phase card is delivered like any card, under the
   **integration** merge rule: its checks run and its review is read at
   the *integrated head*, never a member's, and an independent review
   must differ from every member's implementer. A failed check or a
   request for changes at the integrated head is repaired, where `repair`
   is delegated and within `repairRounds`, by **↻ act on PR** on the
   phase card's own integration branch and PR — the same target contract
   a person's click rides (task 174, "Repairing a phase's integration
   PR"); `repair-unavailable` now means only that the integration PR was
   observed merged or closed. The merge, where `merge` is
   delegated, is merge & clean up — publication confirmed on the remote,
   the members swept to done/ with it.

**Pause and cancel are separate.** Pausing the Bench scope stops every
phase scope's new starts; pausing a phase scope stops that phase only and
leaves standalone work running. Neither stops a run in flight — its
ending is observed. Cancelling is the member run's own **‖ hold**.

**Restart.** An integration whose intent has no receipt is settled from
git — the phase branch holds the head (integrated) or does not (nothing
merged) — and preparing and finishing are idempotent, so an unconfirmed
one is simply asked again. `schedule.phases` in `GET /api/orchestration`
lists every phase with its owner, strategy, integrated members and, for a
scoped phase, each member's state; for a named but unadopted phase,
whether it is adoptable now and what still works it. Reading it launches,
merges and writes nothing. The event wording of a phase on a non-default
base is card 61's, not repeated here.

### A phase's owner, its state, and recovering from a mistaken move

A phase has two supported ways to run (task 160), and the card says
which one it is on:

| path | how it starts | who advances it | the face says |
| --- | --- | --- | --- |
| **the phase runner** | move the phase card into In Progress, then **▸ run phase** | the runner's beat, one member at a time | `not started`, the member in flight, `held` or `halted` |
| **its phase scope** | name the phase in the operating agreement (it inherits the default ceiling), move it into To Do, then **adopt** it in Orchestrator → Phases | the scheduler's passes, under the agreement | the phase scope's lifecycle word and its next step |

Naming a phase in the agreement does not adopt it: until a person does,
it reads `not adopted` and the runner still owns it. Once adopted, the
runner's ▸ run phase and its ‖ hold are not offered on the phase card;
the card offers **⟶ orchestrator** instead, and each member run is
cancelled on its own card.

**One reading for every surface.** `phasescope.lifecycle` is the one
answer, carried by `/api/state` as `phaseLifecycle` and by `GET
/api/orchestration` as each phase's `lifecycle`. The Board's phase face,
the Phases lane and Orchestrator → Phases draw it word for word: the
owner (`phase runner` or `phase scope`), a word, a sentence and the one
next step with who holds it. An adopted phase is never read off the
runner's snapshot, so a scoped phase whose member is running reads
`phase scope — on #157 …`, never "not started".

| word | means | next step |
| --- | --- | --- |
| `not-configured` | the agreement names no scope for it: the runner's | ▸ run phase |
| `not-adopted` | named, not adopted: still the runner's | adopt it, or what still works it |
| `preparing` | adopted; its branch is cut on the next pass | wait |
| `queue-needed` | the next member is in Backlog | move it into To Do. Nothing pulls a card out of Backlog |
| `ready` | a member is queued and may start | the next pass starts it |
| `waiting` | a dependency, a slot (its ceiling or the board's), today's launches, or a ceiling of 0 | the reason says whose |
| `working` | a run is on a member | the agent's; integration follows review |
| `moved-early` | a later member sits in In Progress with nothing working it | move it back to To Do or Backlog |
| `stopped` | a member has work on its branch and no run | Orchestrator → Recovery, or ▸ start work |
| `unknown` | an operation on a member has an unknown outcome | settle it in Orchestrator → Recovery |
| `paused` | a pause, or the scope is off | resume it |
| `integrating`, `finishing`, `integration-review`, `delivered` | a member in review, every member integrated, the phase in review, done | the board's, or the merge requirements' |

A member's readiness in Orchestrator → Phases is the scheduler's own
plan — authorization, capacity, dependencies and the operation ledger —
never the strategy alone, and a member in Backlog reads `in-backlog`,
never `ready`.

**Budgets in plain words.** Each phase lists how many runs it has now
against its ceiling (the default phase ceiling or its own limit), how
many of the board's slots are in use and free, and its launches today
against its daily ceiling and the board's total (task 182). Nothing is
reserved for it: an idle phase uses nothing and its ceiling is never
named as something it holds. (A legacy reserved agreement still says
"reserved out of the Bench scope's total" until it is migrated.) A
launch is one agent run: an implementation, an independent review, a
repair or a diagnosis. So a budget of one launch a day is named as
covering one of those, not a member's whole lifecycle. A ceiling of
zero, or today's launches used up, is said as what is missing and where
to change it. Nothing here raises a limit, queues a member or adopts a
phase.

**In flight means worked, never staged.** Under the sequential strategy
a member is held while another member of the phase is in flight. In
flight is a run on that member, an operation about it without a settled
receipt, work in review, or In Progress *with its own branch*. A later
member moved into In Progress by mistake — no run, no reservation, no
branch — used to hold the first member as `phase-member-in-flight` while
it waited on that member itself, and neither could ever move. Now it
holds nobody: the first member starts, the later one still waits for its
dependency to be integrated, and both the member and the phase read
`moved-early` with the way back.

**The way back is a person's, and guarded.** A phase's members are drawn
in the Phases lane, not on the Board, so the lane's stage columns take a
dropped member exactly as the Board's columns take a card, through the
same `/api/move`. A member that moved early also carries **↩ to to-do**,
armed and then fired. Moving a scoped phase's member out of In Progress
or Review back to To Do or Backlog is refused while a run is on it
(**‖ hold** it first) or while an operation about it has no settled
receipt (settle it in Orchestrator → Recovery), with nothing moved.
Nothing here cancels a run, settles an operation, rewinds a stage or
forces an integration.

### Asking the orchestrator

"Why is card 175 still in To Do?" "What is consuming capacity?" "In what
order will this phase deliver?" A person asks those on the board itself
(task 181): **Ask orchestrator** in the Orchestrator view, or **Ask about
this card** (**Ask about this phase** on a phase card) in a card's
Overview, beside its Orchestration line. Asking explains; it never acts.

**How to ask.** Choose what the question is about — **Board**, a
**Phase** or a **Card** (from a card, that card is already chosen) —
write the question, and press **Ask**; it arms, and the second press
sends it. Above the button the page says what that costs before you
send: the harness and model the `reason` intent resolves to (Settings →
Agents), that it is one read-only reasoning run, and how it is counted.
A reply appears in the conversation; **Ask follow-up** asks again in the
same conversation without restating the subject, and **New
conversation** starts another. Opening the section, reloading it,
choosing a conversation and reconnecting launch nothing: only a
submission does.

**One reasoning run, the shared lifecycle.** A question is the `ask`
reasoning need, asked by a person (`reasoning.request(…, "ask",
via="person")`): the same read-only `reason` intent as every reasoning
run — no worktree, no shell, no edit tool — with its context delivered
by exact reads, the same validation, the same duplicate and retry rules,
and its guidance template `reason-ask.md` editable in Settings → Agents →
Orchestration prompts. There is no second agent stack and no scheduler
behind it. The question itself is data in the facts document the run
reads, never part of the prompt.

**What the run reads.** Bench's own readings at one moment, labelled with
when they were taken: the scheduler's plan for the cards concerned with
each one's state, code and reason (a dependency hold, `daily-starts-
exhausted`, `slots-full`, `not-eligible`, a refused launch), capacity and
today's starts from the agreement in force, the runs in flight as
observed processes beside their last *agent-reported* progress, recent
operations with their outcomes, delivery steps, the circuit breaker and
the last pass; the newest five passes the decision trace recorded about
the cards concerned (any pass, for the board), each decision with its
code, its reason and what the side effect then came to (see "The
decision trace: why it acted, and why it did not"); for a card or phase,
also its brief, phase, dependencies, eligibility and check evidence. A
board question reads the cards in To Do, In Progress and Review. Every
list is capped and says how many it left out, and the readings name what
they exclude — above all that a pass before the trace started, or older
than the passes given, keeps no reason for its choice, so a cause no
record holds is a gap, never a guess. Earlier exchanges of the
conversation are given as history, beside freshly read facts: a
follow-up never answers from a frozen snapshot.

**What an answer is.** Four plain parts — **what is happening now**,
**why**, **what happens next by itself** (if anything) and **who can
unblock it** — then the **recorded facts**, each citing the record it
came from (bench's readings, a card, an evidence record), **what the
records do not say**, and **suggestions**: who could act and through
which existing control. A suggestion is a link to where that control
lives — the card, Recovery, the Operating agreement, Settings → Agents —
with its own confirmation and preflight; nothing in a reply presses it.
The evidence rail opens each citation: a card, or the readings
themselves (`GET /api/ask/facts?operation=…`, read-only JSON). Every
answer is labelled an *agent-reported interpretation*. The contract
refuses an answer that proposes an action or opens a question, a fact
without evidence, a control or a card the run was not given, an unknown
field, an HTML comment or a control character; a refused answer is kept
as a diagnostic, never shown as an answer.

**Outcomes, said as they are.** *Reasoning* while the run is in flight —
its harness, model and how long it has run; the board observes the
process and invents no steps — with **Cancel this question**.
*Answered*; *Answered — no longer current* when the card, the board's
cards, the conversation or the agreement changed since (read it as
history and ask again); *Stale* when they changed while it ran;
*Refused by validation*; *Failed* (the vendor's ending, named);
*Cancelled*; and *Not launched* when the run could not start after the
question was recorded. **Retry this question** asks again, only after a
failure, a cancellation, a refusal or a stale answer — never over a run
in flight or an answer that stands. The same submission sent twice (a
double click, a resend after a lost reply) asks once; a submission
identity reused for a different question is refused, never answered as
the earlier one.

**Before asking, and what it costs.** A harness that cannot run the
`reason` intent (opencode today) refuses the question by name with the
remedy — choose another harness in Settings → Agents; bench never falls
back to another harness or widens a grant. A question takes a reasoning
slot while it runs (an implementation slot where the agreement allocates
none), counted with every running agent; with every slot of that pool in
use it is refused, naming the runs to wait for or hold. A question joins
no admission queue: it is asked now or not at all, and it never takes a
slot ahead of an orchestrator launch already waiting for it — while one
is queued for that pool and would be admitted first, the question is
refused (*queued-behind*), naming it, and can be asked again once the
next pass has admitted it. The capacity reading and the run's start are
one step under the admission lock, so two questions cannot both take
the last free slot. It spends none of the orchestrator's daily starts,
and token use and cost are *not reported*. **Asking needs no orchestration authority**: no agreement, no
delegated word and no designated installation. That a card's
orchestration is off, or that the orchestrator may not act on it, is a
separate fact the answer may explain — and asking changes nothing about
it. One question runs at a time per card, and per board conversation;
while it runs, a recovery diagnosis the orchestrator wants for the
card waits for it, as it waits for a slot, and is not held because of it
— and so does a recovery restart between its diagnosis and its launch.

**Ask is not act.** A question or a follow-up launches one read-only
reasoning run and writes its public conversation, and nothing else: no
card moves, no work starts, stops or retries, no check or command runs,
no code, agreement, gate, budget or authority changes, nothing merges or
is published. Hostile text in a question, a card or an answer is data:
it is escaped where it is shown, quoted in a record, and cannot turn a
suggestion into a step.

**Where replies are kept.** In the work state, synced like any board
edit — never only in the machine-local job records:

| about | kept in |
| --- | --- |
| a card | the card's `## Orchestration record`, as `ask` and `reply` records |
| a phase | the phase card's record (its members are not written) |
| the board | `<work root>/orchestration/conversations/<k-…>.md`, one shared file per conversation, its id stable on every board |

Each write is one `board:` commit under `BOARD_COMMIT_MOVES` and is
published by `BOARD_SYNC`; a write that meets a concurrent edit is
refused and read again, so a teammate's hand edit is kept. A reply keeps
the public fields above, the harness, model and run name, and when the
readings were taken — never hidden reasoning, a raw transcript, a path
or a credential (masked where it is shaped like one). The conversation
survives a restart and losing every runtime record; the run's own job
record stays machine-local, and a reply whose run this board no longer
holds simply has no live state beside it.

| Route | Does |
| --- | --- |
| `GET /api/ask` | the conversations (`?card=` narrows to one card's) and the preflight: harness, model, accounting, readiness. A read |
| `GET /api/ask/thread?conversation=` | one conversation, each reply beside its run's live state and freshness. A read |
| `GET /api/ask/facts?operation=` | the readings one run was given. A read |
| `POST /api/ask` | `{scope: {kind, card?}, text, request}` or `{conversation, text, request}` for a follow-up — records the question, then launches one run |
| `POST /api/ask/retry` | `{conversation, ask}` |
| `POST /api/ask/cancel` | `{operation}` |

The POSTs sit behind the browser-mutation door like every person's
write. `tests/test_ask.py` holds the lifecycle through the Claude
adapter's real `run` against a stub vendor — read-only tools asserted on
the compiled command line, opencode refused by name, card, phase and
board scopes, follow-ups, duplicates, retry, cancel, drift, hostile text
and persistence — and `verify/browser_cases/case_task_questions.py` the page. Whether
the installed CLI with a real model answers well is the opt-in, billable
`develop/reasoning-probe.py`, which nothing runs by itself.

### The Orchestrator view

Everything above has one place a person reads and acts on it (D15, task
145): **Orchestrator**, the third primary destination, at `/orchestrator`. It
owns no orchestration logic. Each section draws what the server answered
— `GET /api/orchestration` (the agreement in force and the draft, every
scope's authority with the scope that decided it, capacity and usage,
eligibility, the schedule, delivery, phase scopes, recovery and the
operation ledger), `GET /api/orchestration/reasoning` and
`GET /api/card/decisions` — and every action goes through the route that
owns it, which decides again. A disabled button is a hint; the route is
the rule.

**Nine sections**, each with an address (`/orchestrator/<section>`, and
`?card=<card>` for the card or phase selected in it):

- **Ask orchestrator** (`/orchestrator/ask`) — a person's free-text
  questions about the board, a phase or a card, and the conversations
  they started (see "Asking the orchestrator" above). Opening it asks
  nothing.
- **Overview** — the one state the whole thing is in, in words (Off,
  Paused, Agreement invalid, Outcome unknown, Recovery needs you, Circuit
  breaker open, Needs your decision, Adoption needed, Read-only here,
  Reasoning, Launching, Working, Nothing committed, Waiting, or Offline
  when the page cannot reach the board), its sentence and the one next
  step; then each scope — Bench, and every phase scope — with its
  authority word by word and the scope that decided it, its slots as
  the board's global total, in use and available, and a phase's own use
  against its ceiling (task 182; a legacy agreement shows its allocation
  and a migration notice), its launches today and its ceilings; the
  admission queue — each waiting request's position, since when, and any
  hold — and which runs hold a slot, a repair of a card in Review
  included; the merge policy, where an ungated
  rule reads **No checks required by your policy** and a delegated merge
  under it *not verified*; and the execution installation.
- **Decision trace** — every scheduling pass this installation
  evaluated, newest first: why it started this card, why not that one,
  and why nothing happened. See "The decision trace: why it acted, and
  why it did not" below.
- **Work** — every card in To Do, In Progress and Review in five groups:
  yours to orchestrate (your last stage move), driven by you (handed back
  or excluded), someone else's, unattributed (who moved it is not known)
  and outside a scope (a phase the runner schedules). A selected card
  shows its eligibility with the movement it rests on, its next step or
  delivery step, its launches against its ceiling, its reasoning job, and
  a run's signals as separate facts — process, age against the warning
  threshold, last progress update (agent-reported), last tool activity,
  a tool call in flight, a Bench check running, last commit.
- **Decisions** — the cards with questions, open first. A question shows
  its text, options, recommendation, why and evidence references; the
  answer form takes an option and a note and is bound to what the page
  read (the question's newest record, the brief, this board's identity).
  A question addressed to someone else, or asked against an older brief,
  is shown and not answerable, with the reason. Resolved questions keep
  their options and answer; reasoning results are labelled
  agent-reported, with proposed actions said to be proposals; a phase
  card lists its members' questions, each answered on its member.
- **Phases** — every phase card: its scheduling owner (phase runner or
  phase scope), strategy, integrated members (*in the phase branch, not
  delivered*), members and their states, and the phase scope's effective
  authority and capacity (parent ∩ phase).
- **Recovery** — the circuit breaker, operations held for a person (an
  unknown outcome; a refused launch), incidents that are held or
  exhausted with their attempts against the cap and the owner's choices,
  and the runs being observed.
- **Activity** — the operation ledger, newest first, and the people's
  orchestration changes on this board.
- **Operating agreement** — what is in force beside the draft, and the
  draft's editor: the Bench scope's enablement, pause, authority,
  capacity, ceilings, exclusions and hand-backs; each phase scope's,
  which may only narrow; adding a phase. **Save draft**, **Preview**
  (two resolutions compared, nothing written) and **Apply** (armed; the
  saved draft, never the page's unsaved edits) are separate steps, and
  **Discard** is its own. The reading carries the document it edits —
  the keys this version reads, notes scrubbed — and a save or apply is
  refused when the revision the page read has moved.

**Every action says what it does, beside its button**, and arms before
it fires: pause and resume of the Bench scope (reaching every phase
scope) or of one phase scope (that phase only); take over manually and
reclaim; exclude and include; **cancel** one named run (‖ hold: that run
and the checks it asked for, branch, worktree and history kept, nothing
else paused or retried); adopt or release a phase; designate this
installation or hand execution over; settle an unknown outcome as
launched or not, or release a refused launch; mark an incident handled;
reset the circuit breaker; answer or withdraw a question. A refusal is
said in the server's words and changes nothing.

**Ways in.** While an agreement is in force the Board carries a strip —
the state, how many cards are managed, how many decisions wait, the
agreement's revision — with **Open Orchestrator** and **Answer
decisions**. A card's Overview has an **Orchestration** line (which
scope holds it, or why none does; handed back; open questions and
blockers) linking to that card in the right section. Both are read from
`/api/state`'s `orchestration` summary (the agreement's status and
revision, each scope's pause, the scope or reason per managed card) and
`decisions` — one file read, no git history, no process.

**One click is one click** (task 147). The view is painted from a
string, so a repaint replaces its controls; a press therefore holds the
view's repaint until the click it belongs to has been dispatched
(`lib/press.js`), and the release asks for the paint it held. A repaint
landing between the button going down and coming up — the focus leaving
the previous control, a reading arriving — used to take the pressed link
or button out of the page and the click with it. The status strip's next
step is never a link to the page already shown, and an armed or busy
button keeps its alarm or accent words over the surface in both themes,
under the pointer and with focus.

**Reading is free.** Opening the view or any section, a card's line and
the strip run no agent, request no check, make no worktree and write
nothing. The view holds its repaint while a field in it has focus, keeps
what was typed and which folds are open across live updates, and works
by keyboard, by touch (44px targets on a touch screen) and at 390px in
both themes. Question text, reasoning summaries and findings are card
text: escaped, rendered by the page's own Markdown renderer, and masked
where they look like a credential — pattern matching, not a promise.

### The decision trace: why it acted, and why it did not

A busy person asks an orchestrator the same things again and again: *why did it
start this?*, *why not that card?* and *why is nothing happening?*
**Orchestrator → Decision trace** answers them in one place (task 184),
from a record of every scheduling pass — not by piecing together the
operation ledger, card histories and today's blockers.

**What a pass is, and what it is not.** A scheduling pass is
deterministic: it reads the cards, the agreement, the ledger and the
capacity in use, applies the rules in this document and acts. It asks no
model. Three different things are therefore kept apart in every entry:

| | what it is | how the trace shows it |
| --- | --- | --- |
| a **decision** | what the pass read and selected, with the rule code and its reason | the entry itself, as written when the pass decided |
| an **agent recommendation** | a bounded reasoning job's public response (a recovery diagnosis, say) | linked by job id and labelled *agent-reported* — validated by bench, never an observed result |
| an **execution** | what then happened: the intent, the receipt, the run, its ending | linked by operation key to the ledger and the card's own history, read as they are *now* |

**One entry per pass.** Newest first, each entry begins with the time,
the scope and cards, the selected action or **Wait / No action**, the
reason, and the outcome. A selection is never called a launch because it
was chosen: *Selected this pass* is planning's word, and *Then:
launched*, *refused at execution*, *outcome unknown* is what the side
effect came to. A card eligible when the pass planned, and refused when
it was re-authorized immediately before the launch (someone else moved
it, a pause, a spent budget), keeps both halves; nothing is rewritten to
pretend it was always blocked. An uncertain launch stays *unknown* — the
next pass holds it, and nothing is launched twice.

Expanding an entry shows, per decision: **Facts considered** (stage,
scope, priority, the brief revision or commit decided on, dependencies,
declared Touches, the queue position, the work base — only what was
captured; a fact not listed is unknown, not absent), **Alternatives
skipped** for a selection or **Selected instead** for a wait (the other
candidates for the same pool that pass actually evaluated, each with its
reason), **Rule or limit** (the code, such as `slots-full`,
`waiting-dependency`, `card-launches-exhausted`), and **Evidence and
links** — the operation and its ledger status now, the run and its
Focus, a recovery incident and its diagnoses, and the card's history.
A wait says what happens next and who takes the step: *automatic* (a slot
frees, a dependency lands, the next pass reconsiders) or *a person* (move
the card yourself, settle an unknown outcome in Recovery, raise a limit
in the Operating agreement), naming only actions that exist and never an
ETA. Passes that were not evaluated — the agreement off or invalid, this
installation a replica, a pass already running — are entries too, said
as *Not evaluated*; a pass that stopped on an error is an *Error —
partial pass* with what it had read.

**Finding an entry.** Filter by card, scope (Bench or a phase), outcome,
action and a date range; **Show older passes** pages back by a stable
cursor, so passes recorded meanwhile never shift a page. Each entry has a
link of its own (`/orchestrator/trace?entry=<id>`), and a card's Overview
has a **Decision trace** link to its own decisions
(`/orchestrator/trace?card=<card>`), which in turn link back to the card
and its history. Opening, filtering, expanding, refreshing and paging
are reads: they launch, reserve, move, fetch and write nothing.

**Repeated waits are folded, never dropped.** A pass that acted on
nothing and decided exactly what the previous one decided is stored as
a small repeat of it. The entry then reads *×N identical passes* with
the first and last time and every pass's id; any of those ids opens it.
A restart, a change in any decision, or anything acted on starts a new
entry.

**What is kept, where, and for how long.** The trace is this
installation's: one versioned JSON line per pass under
`manager/local/state/orchestration/trace/`, never committed and never
synced. The shared, public record of what was done stays the card's
`## Orchestration record` — every intent and receipt the scheduler
writes there, exactly as before — so passes add no card commits and no
sync traffic. Records are bounded: text clipped and scrubbed of anything
credential-shaped, at most 80 decisions a pass, 96 KiB a record.
Retention keeps the newest 8 segments of 512 KiB; what was pruned is
named as the older-history boundary.

**Coverage is said, not assumed.** The trace starts with the first pass
this feature recorded on this board; nothing earlier is reconstructed,
and a board with no agreement at all records nothing. The page says
when it started, the last pass evaluated (apart from the last that
acted, and from any run's tool activity), the scheduler's interval, and
every hole: a stretch with no pass (the board stopped or orchestration
off), a restart, pass records missing from the sequence, unreadable
lines, and passes that could not be recorded (a full disk, say) — counted
and shown on the next entry. A recording failure never changes what the
pass did and never triggers a retry.

| Route | Does |
| --- | --- |
| `GET /api/orchestration/trace` | `card`, `scope`, `outcome`, `action`, `since`, `until`, `before`, `limit` — passes newest first, with the trace's coverage and integrity |
| `GET /api/orchestration/trace/entry?id=` | one entry with every pass it stands for, and what its operations and incidents read as now |

### What is proved end to end, and what stays yours

Each part above has its own tests. The whole journey has one more set
(task 146), in `tests/test_orchestration_journey.py`, and they are worth
knowing about because of what they are *not* allowed to fake. Every
journey runs on a disposable board — an application with a local bare
origin, a work-state repository wherever the layout puts it — in all
three layouts bench ships: **vendored** (the tool inside the
application), **source-hosted** (the tool at the top of the repository
it manages) and **external** (tool, application and cards apart). The
moves are committed with their trailers, the agreement, designation,
lock and ledger are the real files, the launches go through ▸ start
work, ◔ review PR and ↻ act on PR, the checks through bench's own runner,
the merge through merge & clean up and its readback off the remote, and
the agent through the Claude adapter's own `run`. Two things are stand-ins
and nothing else: the vendor CLI behind the adapter, which acts by
intent and logs what it was handed, and the hosting provider
(`tests/product/fakehost.py`). Each step runs in a fresh process, so
each is a restart too.

What they hold, positively, in every layout: a teammate's card becomes
yours only by your move; nothing runs before an agreement; a reasoning
run reads the card through exact grants and its question lands on the
card; your answer is bound to what you saw; the first enabled agreement
launches; the review rides another model in a pinned checkout; an
ungated merge says *not verified* and the remote holds the code; a failed
check is repaired, checked again at the new head and reviewed afresh; and
a phase scope shares the Bench scope's capacity, integrates its members
one at a time and delivers the integrated head under its own rule. And
the failure windows: a crash before a launch (reconciled, launched
once), after a launch (held as unknown until a person settles it, and
no handover meanwhile), after a merge (reconciled, never merged twice);
a stale or foreign answer (refused, nothing written); a head that moves
under an approval (asked about afresh); someone else's move (never
acquired, and it stops the next action); a pause; a spent daily quota;
a run that keeps dying (two retries, the breaker, one bounded diagnosis
held for you); and default off. `tests/test_orchestration_install.py`
holds the distribution: every engine module and the view ship, a fresh
or repeated install is off, and an update keeps the agreement, the
designation, the cards and the ledger byte for byte.

What none of that proves: how a real model behaves, or whether the
installed vendor CLI honours the permission rules the adapter generated.
Those are the opt-in, billable probes `develop/reasoning-probe.py` and
`develop/claude-access-smoke.py`, which nothing runs by itself. A
passing journey is evidence about bench's own lifecycle, not about the
agents it launches.

**Maintenance boundaries.** Turning orchestration on, designating the
installation that executes, handing execution over, adopting a phase
from the phase runner and resetting the circuit breaker are each a
person's act. Promoting a new bench into the installation that serves
the board, publishing a release and deploying the website are separate
again: no orchestrator, journey or test does any of them. A phase
already running under the phase runner is never switched to its scope
mid-flight; it is adopted, explicitly, once it is quiet.

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
  This one review is the ordinary acceptance assessment: its contract
  asks it to assess **each acceptance criterion** against that exact
  head, from the diff, the code and substantive evidence together (a
  file and line, a test it read, a result bench recorded at the head —
  never a report's "tests pass"), and to list what it could not judge
  and why, in an *Acceptance assessment* section of its report. That is
  a reviewer's assessment, not a person's acceptance: the run is told to
  record none, it gains no grant for it, and a sign-off where one is
  required is still a person's (see "Sign-off: a person's decision, only
  where a rule asks for one").
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

### Which host: hosting providers

Everything above goes through a **hosting provider** (task 116):
`githost.py` picks the one in force and `providers/<name>/` holds what
bench knows about that host — for GitHub, every `gh` argument list, url
and payload shape. `changes.py` (formerly `github.py`) keeps the
lifecycle and reads normalized values back, so the words on the board,
the `**PR:**` line, the ticker sentences and the `github` /
`separate-reviewer` review policies are exactly what they were.

- **`BOARD_GIT_PROVIDER`** — `github` by default; `none` works the
  application with git alone: no PR is opened, polled, reviewed on a host
  or merged there, no PR chip appears, no launch is granted a `gh` verb,
  and a plan reads `local-only` with that reason. Merge & clean up's
  local merge is unaffected.
- **An unrecognised remote is refused, not guessed at.** The GitHub
  provider acts on a remote on `github.com` (or a host named in
  `BOARD_GITHUB_HOSTS`) and on a local path remote; a remote on any other
  host is reported in Settings and in the Configuration check, and `gh`
  is never run against it.
- **Unknown authorizes nothing.** Reading a status or opening a page runs
  no probe and no fetch; whether a merge can be made conditional on a
  head is asked only when a merge needs to know, and a capability that
  reads `unknown` refuses the mutation by name.

The contract is `manager/core/providers/README.md`; how the provider's
verbs reach a coding agent (`AGENT_HOST`) is in
`manager/core/adapters/README.md`.

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
  own PR, where **↻ act on PR** repairs it (see "Repairing a phase's
  integration PR").
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
the verdict is yours — in the card's **Reviews**, where you can comment
on it and ask for the card to be updated from it (see "Relevance
reviews: comment, then update the card from one"). One agent per task at
a time applies across both kinds.

### Repairing a phase's integration PR

When a phase's integration PR is asked for changes, its members' own PRs
are merged already — there is nothing to push to on a member, and nothing
to reopen. So **↻ act on PR** on the phase card itself repairs the
integration work in place (task 174): the same action, the same arm-then-
fire confirmation, the same `act-pr` intent, profile, model, prompt
template and grants as on an ordinary card. One resolver,
`agents.repair_target`, answers what is repaired for every door — the
card's button, its launch check and a delegated repair an orchestrator
scope selects — so the three cannot disagree.

| | ordinary card | phase card in `review/` |
| --- | --- | --- |
| branch | `task/<stem>` | its existing `phase/<stem>`, never a task branch |
| worktree | the card's, recreated from the branch if cleaned up | the phase's Bench-managed worktree, recreated from `phase/<stem>` if cleaned up |
| pull request | the card's | the open integration PR — updated, never replaced |
| refused when | the PR is merged or closed | the same, and every case below |

A phase repair is refused, naming its prerequisite, with no branch,
worktree, claim or run created and nothing reset, force-pushed or
switched:

| code | what it means | the way through |
| --- | --- | --- |
| `no-pr` | the phase has no integration PR | **↑ open PR** on the phase card |
| `pr-merged`, `pr-closed` | the integration PR has ended | open a card for the change |
| `head-drift` | the PR proposes another branch, or a head this checkout's `phase/<stem>` does not hold | fix the PR, or fetch and fast-forward the branch |
| `base-drift` | the PR targets another base than the board recorded | ⌖ replan, or change the PR back |
| `no-branch` | `phase/<stem>` is not here | **Check the remote** |
| `foreign-checkout`, `wrong-branch` | something else sits where the worktree goes, or it is on another branch | settle it by hand; bench never switches it |
| `dirty`, `conflicting` | the worktree has uncommitted changes, or a merge or rebase in progress | commit, remove or finish it by hand |
| `busy` | a run is on the phase or a member, or a repair is already being started | **‖ hold** it, or wait for it to end |
| `operation-pending`, `operation-unknown` | an orchestrator operation on the phase or a member has no settled receipt (an `unobserved` run a restarted board never saw end included) | wait for it, or settle it in Orchestrator → Recovery |

**When the host cannot be read.** Without an answer from the hosting
provider the target is the phase's own branch in this checkout, as it is
for an ordinary card, and the launch check says the PR's head and base
were not compared. The repair's push is a plain one, so a remote branch
that moved on without this checkout refuses it and nothing is forced over
it; the run fails, the commit stays here, and the card stays in Review.

**What stays where it is.** The phase and every member stay in
`review/`; the member list, the phase log's integration lines and the
members' PRs are untouched. The run is told it is repairing the
integration PR, given the card (its brief and the integration review
filed on it) and its pinned approved inputs, reports its progress on the
phase card, and files its `## PR update` there. The card's delivery
status offers the action with the branch it repairs; a phase card is
never told to move back to in-progress, which would start no supported
run.

**A new head is asked afresh.** The repair's push is a new integration
head, so the checks recorded and the review given at the old one are
history: the merge rules ask for the integration rule's checks and a
fresh, independent review of the exact updated head (the repair run
counts as an implementer). A repaired or approved phase still merges into
the application base only by merge & clean up under your merge rules, and
none of this promotes the operator, activates a policy or authorizes a
later phase.

**Orchestration.** In an adopted phase scope, a failed check or a request
for changes at the integrated head is repaired where the agreement
delegates `repair`, within `repairRounds` and the phase's daily starts,
as an operation in the ledger like any repair; without the word, the
card reads `repair-not-delegated` and **↻ act on PR** is yours. Its own
operation is not counted as a competitor. A run on the phase or a member,
or an operation on one still in flight, is read by the plan itself
through the launch's own check (`agents.phase_repair_wait`): the card
reads `repair-waiting` (or `repair-held`, for an unknown outcome) and no
repair operation is opened — so nothing is refused and recorded on the
card pass after pass. A launch that still loses that race is refused
`busy` or `operation-pending`, and that refusal is answered afresh on the
next pass rather than held.

### Relevance reviews: comment, then update the card from one

A relevance review judges the card's *definition*; a PR review judges
code at a revision. The detail view's **Reviews** section keeps them in
two labelled groups, each with its own empty state, so a card whose only
review is a relevance review never reads "no review". Each relevance
review shows its own verdict words, its reviewer, when it was filed, its
stable identity (`rl-…`, a hash of its heading line, so every board
computes the same one) and the revision of the brief it read — *not
recorded* for every review filed so far, never a guess. A heading in an
older format (a date, no time or reviewer) is shown as written. Its
report is drawn by the same renderer as every other report. A relevance
verdict approves nothing: it changes no PR verdict, no acceptance count
and no merge requirement. Activity dates each review and links to it,
and so does the Overview; neither draws a second copy.

Under each relevance review are two actions, deliberately two:

- **Comment** saves your note to the card's discussion, linked to that
  review (the entry gains `"review": "rl-…"`). Nothing else: no agent,
  no requirement edit. A repeated click is one comment.
- **✎ Update card from this review** (armed) saves your instruction —
  or, left empty, "apply this review's recommendations" — as a comment
  linked to the review, and asks the existing `refine` intent for one
  proposal, sending it that review's report beside the instruction. It
  is not another relevance review and not an implementation run. The
  proposal, its diff and **Apply**/**Discard** are exactly those of
  "Refining a card"; the job and the decision name the review they came
  from. Applying records what changed — it does not say every
  recommendation was met, resolves no finding and approves nothing.

A comment already linked to a review carries that review into its
refinement and its **↻ Retry**. A request naming a review that is not on
this card, or a comment that is about a different review, is refused
with nothing written. A proposal whose review is no longer on the card
reads stale, like one whose brief moved. The comment and refine routes
take a JSON body only, and refuse a stated origin that is not this
board.

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

### After the host merged it: delivery is not publication

Task 166 closed a hole that made two merges of one card. The host merged
a card's pull request; this board's cached PR status still said `OPEN`
(or, after a restart, nothing); **merge & clean up**, in the local merge
mode, merged the same branch into local `main` again — a second merge
commit with the same tree and a different SHA. Its push was refused, but
the remote's `main` already contained the card's *head* through the
host's merge, and the publication check asked about the head. So it said
"published, the reply was lost", cleaned up and moved the card, and the
next card's PR was refused because local `main` was ahead of the remote.

**Two different facts, read separately.**

- **Delivered** — the application remote's target holds the card's exact
  head. Before any merge, the completion asks the remote that, whatever
  the cached status says. If it does, the completion *reconciles*:
  nothing is merged, no merge rule is asked for (it guards a merge this
  board would make, and there is none), the workspace is cleared under
  the usual rules and the card moves. The result says
  `reconciled: true`, `merged: false` and outcome `delivered`. In the
  local merge mode, a remote that cannot be read at that moment refuses
  the completion with nothing merged: a merge made blind to whether the
  work is already delivered is the one this exists to stop. On origin
  the host itself will not merge a change twice, so it goes ahead as
  before and its publication is read afterwards.
- **Published** — the remote holds *the merge this board made*. A local
  merge is published only when the remote's target holds the new local
  base tip, the commit a push publishes. The remote holding the card's
  head through another merge does not prove that, and is never read as
  it. A lost reply is recognised only when the remote holds that exact
  merge.

Each completion result and record keeps three identities apart: `head`
(the card's commit), `merge` (the local merge this completion made,
empty on origin) and `remoteTip` (what the remote's target was read
at). The outcome is one word:

| outcome | what it means | the card |
| --- | --- | --- |
| `delivered` | the remote already held the head; nothing was merged | done/ |
| `published` | the remote holds the merge made here | done/ |
| `local-only` | no application remote; nothing was published | done/ |
| `rejected` | the remote refused the push | stays in review/ |
| `diverged` | the remote's target has commits this checkout's lacks — including the head through another merge (`deliveredElsewhere`) | stays in review/ |
| `unavailable` | the remote could not be read | stays in review/ |
| `unknown` | whether the remote holds the merge is not established | stays in review/ |

**Recovering from a duplicate an older board left.** When the record says
`deliveredElsewhere`, the card's work is on the remote and the local
merge is a second copy of it. Nothing is cleaned up, and bench pushes,
resets and force-pushes nothing. The sheet, the card and the record's
`action` name the next step: keep the local merge under a reference of
your own (`git branch recovery/<card> <merge>`), bring the application's
`main` back to the remote's `main` yourself, then **merge & clean up**
again. It reconciles without merging. A card whose PR could not be opened
meanwhile keeps the actual reason (the base ahead of the remote, a
refused push) on its delivery status and in any orchestration blocker,
and **↑ open PR** is the retry once the base is repaired. Nothing retries
it, or any historical push, by itself, on a replica or at a restart.

**Catching the base up.** After a delivered or origin completion,
`baseCatchUp` says what became of the application checkout: `synced`
(fast-forwarded), `sync-beat` (vendored with ticket sync: the sync beat
owns it), or left exactly as it was — `wrong-branch`, `dirty` (tracked
changes), `diverged` (it has commits the remote lacks) or `failed`. It is
never reset, rebased or merged into.

**Local or origin is the operator's policy, unchanged.** `BOARD_APP_MERGE`
still decides where a merge is made, and ticket sync still does not. Both
modes get the delivery check first; neither is switched by the board.

**Who asked, on the record.** Every completion is an operation with an
id (`cmp-…`), a source (`api` for the board's endpoint, `orchestrator`
for a delegated merge) and an actor *only when one is verified* — the
orchestrator's git identity. The endpoint authenticates nobody, so its
default "you" is never written down as a person; the sheet says *actor
not verified*. The recovery record keeps the operation, the three
identities, the outcome, the scrubbed detail, the next step and the last
twenty attempts. The delivered record keeps the operation and the
identities too. Neither holds a path, a credential or a prompt.

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
- A comment about a relevance review carries `"review": "rl-…"`, the
  review's identity; a decision on a proposal asked from one carries the
  same field. A comment saved with the page's request identity carries
  `"request"`, so the same request again — after a restart, or on a
  replica — answers with the comment already written. Both are optional:
  an entry without them reads exactly as before, and nothing rewrites an
  entry to add them.
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

Where a merge rule *does* configure one (`"signoff": "criteria"`, see
"Sign-off: a person's decision, only where a rule asks for one"), the
Acceptance section leads with **Sign-off required**, what it reads now
and its remedy, and the gate holds the merge until every tracked
criterion reads `accepted` against the commit that would merge. The
records are the same records; only the rule reading them is new. A run's
report can never add one: a report line shaped like a board record
(`<!-- bench:…`) is quoted before the report reaches the card, so it is
still there to read and is never parsed as a decision.

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

Where the members went. **Board › Phases**, beside Board › Tasks in the
Board's bar, holding **one lane per phase card** — every phase there is, not only the
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
  drawer — and two links that change nothing: **Sessions** (the phase's
  and its cards' sessions) and **Coordinate** (the phase in the
  Orchestrator). Its number drills into the phase: `/phases/<card>`, the
  lane drawn alone under Board › Phases › the phase, with a card opened
  from it returning to it. Nothing here ends a phase — merging is a
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

**A lane whose every card is merged in closes itself** (task 161). Once
every number the phase lists resolves to a card on this board and is in
`Merged in` — the very placement the columns and the head's count read —
the lane is drawn as its head, any halt or gate line, and one summary:
*All N cards merged into the phase branch — the phase itself is not
delivered*, with the phase's own status (its delivery word), its PR and
**Open the phase card**, where its next step is; a phase in `done/` reads
*Delivered*. That happens on first load and on the live update that
brings the last card in. The columns and the log are kept, hidden, behind
a **Show cards** / **Hide cards** toggle on the head (a button with
`aria-expanded`, by pointer or keyboard). A person's choice stands for the
page session, across redraws and reconnects, while the lane stays
complete; a card that goes back to unfinished work, or a newly listed one
that is not merged, opens the lane and forgets the choice, so the next
completion closes it again. A phase that lists nothing, names a number no
card on this board has (an archived member included), or wears any other
`phase drift` is never read as complete, and a halted lane is not closed
for you. Closing is presentation only: it moves, merges, archives and
launches nothing, and is remembered nowhere but the page.

**A halted lane says so at the top of itself**: the reason, the card it
stopped on, and **▸ run again** beneath them. That action lives here and
not on the Board, because clearing a halt should mean having read what
caused it — the Board's halted phase card offers **⟶ phases** in its place,
and **‖ hold**, which needs nothing read.

The crossing matters more than the lane: a person on the Board has to
learn that a phase halted without being on this view, and a person here
has to be able to get back to the card that owns it. So the Phases tab
carries the running count and, in `--alarm`, a mark when something has
halted, and the primary **Board** carries the same mark — on every
destination, holding until the phase is run again or held,
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
this is bookkeeping, not code). A move whose commit failed — another git
holding the index, say — has still happened on disk, and is said in the
ticker; the card's next commit records it. A next move also records the
removal of any path of that same card git still has — in its index or
in HEAD, since a commit that failed after staging leaves the deletion
staged — and the disk no longer does; a next edit (a report, the `**PR:**` line, an orchestration
record) records that removal under the failed move's own trailers from
the movement journal, so the card reads as moved by whoever moved it
rather than created where it landed. A path the board did not move — a
hand `mv` — is never attributed this way. So a failed commit never leaves
a stale task path behind for sync or a merge to stop on.
Pushing is not part of it: those commits sit
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
| **Touches** | no | paths or areas the work changes, comma-separated; absent is *unknown* overlap, never safe | you |

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
open questions, whatever is relevant to the current stage — with one
section the board reads: `## Inputs and required tools`, the card's
contract for what the work needs to read and use. One line per input or
tool (`- input, required: ../../reference/api.md — why`, `- tool,
required: check unit`); a link in the brief it does not list is proposed
and counts as required until classified. Implementation does not start
while anything required is unusable — see "Required information: inputs
and tools".
