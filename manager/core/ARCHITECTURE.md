# Bench architecture

How `manager/core/` is put together today, where it is meant to go, and
the rules that keep it from drifting while it gets there. The three
fenced `architecture` blocks below are read by
`tests/test_architecture.py`, which is their only reader and the only
place the rules are enforced: edit a block and the test follows it. The
prose is for people.

This map was produced for task 107 (WP01 of the maintainability plan)
from the tree at `df72007`. It describes; it changes no behaviour.

## The convention

**`A → B` means A imports B.** B is therefore the lower-level module, and
a layer may import only from layers with a smaller number. Arrows never
mean execution order or data flow here. (The one-line module map in
`AGENTS.md` therefore separates layers with `<`, lowest first, rather
than with arrows; a test keeps it equal to the table below.)

Two inventories are kept apart on purpose:

- **Current facts** — what the code imports now. The current layer table
  must place every core module that exists and nothing that does not,
  and every top-level import must point strictly down it. Where the code
  does not obey the table, the exception is listed in the test's
  reviewed baseline, never hidden by moving a module to make it look
  compliant.
- **Proposed layers** — the target of the maintainability plan. Modules
  that do not exist yet are named `new` and are not required to exist;
  nothing is checked against the proposed table except that it accounts
  for every current module (placed, or `retired`).

## Current layers

Lowest first. A module on `Ln` imports only modules on `L0`…`L(n-1)` at
the top level. `composition` modules are imported by nothing outside
composition.

```architecture current
L0  gitproc records redact
L1  config
L2  githost assets agreements operations
L3  state reports templates gitrepo verifierreport workbase
L4  taskfiles
L5  plans profiles execowner movements decisions
L6  gatepolicy reviews inputs basechoice
L7  evidence resultidentity artifacts
L8  staleness
L9  verify
L10 progress
L11 acceptance callers
L12 gates
L13 required orchestrator
L14 doctor
L15 commands drive events sync
L16 changes
L17 completion
L18 stacks
L19 delivery stackactions results cycletime
L20 agentsettings
L21 settings
L22 setupbase appinspect
L23 agents previews readiness
L24 setuptasks
L25 setupguide
L26 phases
L27 phasescope
L28 refine reasoning coordinate
L29 recovery
L30 scheduler
L31 routes watch
L32 httpd
L33 board
composition: board httpd routes watch
```

`evidence`, `resultidentity` and `staleness` (task 121, WP12 — the verify
train) sit between `reviews` and `verify`, where the target table puts
them. `evidence` is the record: where it lives, how it is stored (through
`records`, durably once final) and read, the reuse records, the outcome
vocabulary and the evidence epoch (`evidence.epoch()`, which `gates` and
`delivery` key their memos on). It imports `config`, `records`, `redact`
and `state`. `resultidentity` is what a verdict is about — every identity
dimension and the one owner of the runner revision, a digest over
`RUNNER_FILES`; it imports `config`, `gitproc` and `profiles`, and its git
goes to `gitproc` directly so the code that reads a source identity is
inside that revision. `staleness` is whether a stored pass still holds,
and declared equivalence; it imports both, `profiles` and `reviews`.
`verify` keeps the runner: the request, the reservation registry and
reuse selection (together, under one lock), the execution, cancellation,
recovery and the board's reading. Consumers ask each owner directly;
nothing is re-exported, and reading a moved name on `verify` raises,
naming its owner.

`stacks` (task 117, WP09) sits directly above `completion` and below
`delivery`, where the target table puts it: the derived stack reading —
each edge's state, a card's place on its path and its three targets —
read from the recorded plans (`plans`), the PR snapshots (`changes`) and
the delivered records (`completion`), with local git only. The graph
itself (parent, children, loops) is derived once by
`taskfiles.weave_stacks`, beside `weave_phases`, so a loop reaches
`plan drift` and preflight refuses it without `plans` reaching up.
`delivery` and `routes` read it; it reads nothing above itself.

`stackactions` (task 119, WP10b) sits beside `delivery`, directly above
`stacks`, and neither imports the other: it performs the three explicit,
confirmed stack actions a reading offers — **Retarget onto <target>**
(through `githost.retarget_change`, only on `available`, else the
manual step), **Update from <parent>** (an additive merge in the child's
worktree) and **Finish: clean up** (through
`completion.finish_delivered`, after `completion.remote_holds`). It
records a retarget with `plans.retarget`, writes the card's
`**PR base:**` through `taskfiles.set_pr_base` and tells `gates` to
re-read (`gates.invalidate`). Only `routes` imports it.

`gatepolicy` (task 120, WP11) sits directly above `profiles`, beside
`reviews`, where the target table puts it: reading and validating the
document's `"gates"` block, moved out of `profiles` verbatim. It imports
`profiles` only — the document, the checks a requirement names, the
diagnostic codes, `Trouble` and `digest` — and owns no evaluation
(`gates` does). `profiles` keeps the scope vocabulary (`GATE_SCOPES`)
and its one shallow read of the block (`verified-outputs`), and is
handed the merge rules' prompt lines by every launch
(`gatepolicy.required_lines`) rather than reaching up for them. Every
persisted identity it computes is pinned by `tests/test_policy_golden.py`.

`inputs` (task 132) sits directly above `profiles`, beside `gatepolicy`
and `reviews`: a card's required information — the `## Inputs and
required tools` contract in its brief, the references the brief names,
whether the run that would start can use each of them, the bounded check
that retrieves a remote one, and what a launch is given. It imports
`config`, `profiles`, `records`, `redact`, `state` and `taskfiles`, and
nothing above them: `agents` asks it before a launch and adds its inputs
to the manifest before the pin, `routes` asks it before a move into
in-progress and for the Inputs tab. The policy lives here and not in
`taskfiles.move_task`, which stays a primitive.

`verifierreport` (task 135) sits beside `reports`, low enough that
`profiles` (which validates a check's `"report"` declaration), `evidence`
(which merges its reasons into the outcome vocabulary) and `verify`
(which reads a report and combines it with the exit status) can all
import it. It owns the version-1 verifier report contract — the envelope,
its bounds and the one consistency rule between a report and an exit
status — and imports `redact` only. It is inside the runner revision
(`resultidentity.RUNNER_FILES`), because it decides outcomes.

`artifacts` (task 133) sits beside `evidence`, directly below the
runner: retained demonstration outputs — what a check declaring
`"outputs": true` wrote to `$BENCH_VERIFY_OUTPUTS`, kept under
`state/artifacts/<card>/` with the run that made it, classified by its own
bytes, bounded, withheld or expired with its record kept. It imports
`config`, `records`, `redact` and `taskfiles`; `verify` asks it to keep a
run's outputs once the verdict is already written (it is named in
`resultidentity.BOUND_ELSEWHERE`, not in `RUNNER_FILES`), and `results`
and `routes` read it. `results` (task 133) sits beside `delivery`: a
card's **Show the result** — the `## Show the result` request in its
brief, its kept outputs read against the current commit, the checks bench
ran, acceptance counts and what is known of merged, installed and
deployed. It imports `acceptance`, `artifacts`, `changes`, `completion`,
`config`, `evidence`, `gates`, `profiles`, `records`, `redact`,
`staleness`, `state` and `taskfiles`; `agents` asks it for the prompt
paragraph, the manifest's frozen request and the handoff rule, and
`routes` for the Overview's reading and the move into review.

The scoped orchestration engine (task 137, phase 136) is three modules,
each below its callers. `agreements` (L2, `config`, `records` and
`redact` only) is the operating agreement: the versioned document, its
validation, its revision, and the effective reading — authority as the
intersection of the Bench scope and a phase scope, pooled capacity as
allocations of the parent's total, ceilings that may only be lowered,
pause inherited downward — plus draft, preview, apply and the
operational toggles, each under a revision guard. `execowner` (L5,
`config`, `records`, `taskfiles`) is who executes: the portable
designation in the work state, the non-blocking OS lock, controlled
handover and adoption; it never reads a heartbeat as authority.
`orchestrator` (L13, above `gates`) composes them with the board and the
merge rules into one deterministic `authorize` and the public `reading`
that `GET /api/orchestration` serves. `movements` (L5, `config`,
`gitrepo` and `taskfiles`, task 138) is whose a card is by its last person's stage
move: it reads back the `Bench-Move` trailers and the uncommitted-move
journal `taskfiles` writes, and `orchestrator` asks it for both the
launch path and the reading. None of them launches anything;
the scheduler and ledger are later members of the phase and ask
`orchestrator`. `reasoning` (L27, beside `refine`, task 139) is the
reasoning contract: a bounded `reason`-intent coding-agent job through
`agents.launch`, its context (the card, its phase, its dependencies and a
facts document from bench's public readings) pinned by `profiles.pin`
and granted by exact reads, and the one public, versioned response bench
validates against the binding it launched with. It asks
`orchestrator.authorize` before an orchestrator's launch and records it
in the launch ledger; it executes nothing a response proposes.

The engine's acting half is two more (task 141). `operations` (L2,
`config` and `records` only) is the durable operation ledger: one
versioned record per orchestrated side effect, written durably as an
*intent* before it and updated with the observed *receipt* after, under
an interprocess lock; its statuses say which records reserve a slot and
which spent a start, so `orchestrator.usage` counts reservations and
budgets from it and a restart, a pause or a lost reply resets nothing.
`scheduler` (L28, directly below `routes`) is the bounded pass —
observe run endings, reconcile intents without a receipt against the run
registry, the recorded plan and the process table, plan by dependencies,
priority, fairness and declared `**Touches:**` overlap, then execute
through `taskfiles.move_task` and `agents.start_agent` with
`orchestrator.authorize` asked again immediately before each side effect.
It has no git of its own and no second launch path. `board` starts its
beat; `routes` serves its read-only projection and a person's settlement
of a held operation.

`phasescope` (L27, task 143) is what a phase scope adds to the shared
engine, above `phases` whose mechanics it reuses rather than repeats:
adoption from the phase runner (a line in the phase log, refused while
the runner, a member's agent or an unsettled operation still works the
phase), which member may start under the phase's strategy, the phase
branch cut through `phases.ensure_branch`, a member integrated through
`phases._merge_member`, and the phase finished through `phases._finish`.
It launches nothing: `scheduler` starts members and `coordinate`
integrates them and delivers the final integrated head, both asking it.

`coordinate` (L28, beside `reasoning`, task 142) is the delivery half of
a pass, for standalone cards already in `review/`: the candidate's
required checks through `required.start`, an independent review through
`agents.start_pr_review` once the reviewer's resolved model is shown to
differ from every implementer's (by the names each harness declares in
`capabilities.json`, read by `profiles.adapter_identity`), a bounded
repair through `agents.start_pr_fix`, and a delegated merge through
`completion.complete_task` with the exact head pinned — or
`completion.complete_delivered` when the remote already holds it. Each
step is an `operations` record keyed by the candidate head, authorized by
`orchestrator.authorize` immediately before it acts; `scheduler` calls it
after its own launches and above it, so the engine still has one beat.

`profiles.read_scope` (task 127) is a request-scoped memo, not a layer:
a `ContextVar` that `routes.state_payload` sets for the length of one
board read, inside which `document()`, `resolve()` without a draft, an
approved input's reading and its `git log` provenance are each computed
once per key — the key being what the answer depends on, the files'
`stat` included. No other module opens one, no mutation path reads
through one, and nothing in it outlives the request. It lives in
`profiles` because every question it memoises is `profiles`' own, which
keeps the runner files (`resultidentity.RUNNER_FILES`) — and so every
recorded pass's runner identity — untouched.

`githost` (task 116, WP08) sits directly above `config`: which hosting
provider is in force, what it can do here, and refusing by name. It
imports `config` only, and loads a provider (`providers/github/`,
`providers/none/`) by path; a provider imports nothing from core. It sits
*below* `templates`, which asks it for the prompt fragments the PR
templates carry — the one place the target table (where `githost` is
above `gitrepo`) is reordered, because the provider's words reach a
template before any repository is asked anything.

This is the order `AGENTS.md` states, with two facts made explicit:
`github` imported `drive`, so the old `events / github / drive / sync`
tier is split with `github` above it (since WP07 it is `completion` that
imports `drive`; `github` keeps its layer); and `commands`, which the old map
omitted, sits with the other leaf services on `L12`. Every distinct
top-level core-to-core import edge obeys it (176 at WP01).

`completion` (task 115, WP07) sits directly above `github`: merge & clean
up — the merge, its publication, the recovery record, the cleanup and a
phase's sweep — moved out of `github.py` verbatim, and it asks `github`
about the pull request through public accessors (`pr_state`, `forget_pr`,
`pr_info`, `merge_pr`, `merge_is_conditional`, `woven`) rather than
reaching into `PR_STATE`. `delivery` and `routes` ask it for the recovery
and delivered records and for the merge sheet's facts.

`assets` (task 123, WP14) sits beside `githost`, directly above
`config`; it imports `config` and `records` (which writes the
manifest atomically): the page's asset manifest, the build
id and the boot snapshot the board serves under `/web/<build>/`. `board`
asks it to boot before the port is bound (a refusal means the board does
not start) and `routes` asks it for the page, the build and each asset.
It covers the page's files only; the Python code a running board
executes is pinned by eager startup (task 122), not by this.

`gitproc` (task 109, WP03a) is the first stdlib leaf: the hardened git
subprocess layer, moved verbatim out of `config`. It imports no core
module and only the standard library, which `tests/test_architecture.py`
holds it to. `config.git_argv`, `git_env` and `git_run` remain real
functions that call it, so patches on `config` keep intercepting.

`redact` (task 111, WP03c) is the third: one table of what a credential
looks like — the union of the tables `settings`, `agentsettings`,
`previews` (through `verify`) and `bootstrap.py` kept apart — with the
marker passed in, so each caller keeps the words its readers see
(`settings` its `••••••••`, `previews` its `«redacted»`). `settings`,
`agentsettings` and `previews` delegate to it, and since the verify train
(task 121, WP12) so does `evidence.scrub`, which `verify`, `doctor` and
`progress` ask — verify's own copy is gone. `bootstrap.py` runs before an install
exists and keeps its copy; `tests/test_redact.py` holds the union to
every pattern of all five.

### Where the code did not obey it, and how it does now (task 122)

Until WP13 the order held at the top level only because some imports
were deferred into functions, and some modules reached private names of
others. Both lists are **empty** now, and a non-empty one fails the test:
any function-level core import — upward *or* downward — and any
`module._name` used from another module is "new, not in the reviewed
baseline". Each was removed by giving the name a public owner, never by
an alias, a wholesale re-export, or a module moved to look compliant.

| What it was | What it is now |
| --- | --- |
| `setupguide` importing `readiness`, `previews` and `setuptasks` from six functions, while `readiness` and `setuptasks` reached 27 times into `setupguide`'s private helpers | the shared helpers — the decisions record, the journal, request identity, the revision and integrity guards, the actor, the agent area — moved verbatim into **`setupbase`** (L22) under public names; `setupguide` moved *up* to L25, above the three surfaces it composes, and imports them at the top. The plan's note that `setupguide` would stay below them was a design to validate: the guide's areas (`_verification`, `_preview`) and its one reading (`state`) are built from those surfaces, so below them it could only have reached up |
| `setuptasks.gaps` importing `previews` while `previews.discover` imported `setuptasks` | the read-only inspection of the application (`inspect` and its file lists) moved verbatim into **`appinspect`** (L22, `config` only); `previews` and `readiness` read it there, so `setuptasks` sits above `previews` and imports it at the top |
| `drive` importing `previews` in four functions | scrubbing a drive's log is the drive's (`drive.scrub`, with the `«secret»` and `«redacted»` markers a preview's reader always saw), because the secret values and the log are the drive record's. What a preview keeps once it ends (`previews.record_last`) is registered with `drive.on_ended` by **`board.wire`** — the one piece of composition wiring, explicit and idempotent |
| `settings.frozen_runs` importing `agents` | it reads the run registry where it lives, `state.AGENTS`, producing the same rows |
| `board` (×2) and `setuptasks` (×4) deferring downward imports | top-level imports |
| `taskfiles._is_fence`, `_one_line`, `_refuse_escape`, `_depends_on`, `_first`, `_commit` | `is_fence`, `one_line`, `refuse_escape`, `depends_on`, `first_match`, `commit_paths` |
| `agents._launch`, `_pick_name`, `_finish`, `_why` (refine), `_validate`, `_preflight_profile` (setuptasks) | `launch`, `pick_name`, `finish`, `died_on` — the launch primitive `refine` rides — and `validate_launch`, `preflight_profile` |
| `gates._tips`, `_contained`, `_EPOCH` (delivery) | `gates.branch_tips`, `contained`, `epoch()` |
| `settings._checks_of`, `drive._alive`, `config._SUFFIX` | `settings.checks_of`, `drive.alive`, `config.PURPOSE_SUFFIX` |

**Eager startup.** `board.py` imports every core module at its top level
— check 11 fails a core module it does not — and loads the selected
hosting provider, plus `none`, which `githost.agent_verbs` falls back to
when the selected one does not act on the remote (`board.load_provider`,
which reads the providers' code and declared functions and asks no
network, `gh` or remote), before it serves. A running board therefore executes the code that was on disk
when it started; a core update takes effect on restart, all of it at
once. `tests/test_eager_startup.py` holds the running process to that:
every core module and the provider are in memory after startup, and the
imports, the provider load and the wiring start no thread, run no
process and open no connection.

**What still remains, and why.** `config.git_argv`, `git_env` and
`git_run` (over `gitproc`) and `config.branch_for`, `dirty_files` and
`worktree_mismatch` (whose bodies `gitrepo` offers under the same names)
stay in `config`. They are not exceptions to the layer order — nothing
there imports upward — but they are the bridge the plan meant WP13 to
remove, and it is not removed here: `verify.py` calls the three readers
(and `staleness` and `resultidentity` call two of them), and moving them
means editing `verify.py`, which only the WP12 delivery unit may do and
whose runner revision (`resultidentity.RUNNER_FILES`) would change with
it. `config.git_run` is also the seam the suite patches to intercept
git. Removing the bridge is coordinated with the next change to the
verify train; until then a running board loads it at startup like
everything else.

## Proposed layers

The maintainability plan's target (§2.2), recorded so later packages can
be checked against it when they land. Not enforced.

```architecture proposed
L0  gitproc records redact
L1  config
L2  state reports templates gitrepo assets verifierreport agreements operations workbase
L3  githost
L4  taskfiles
L5  profiles plans execowner movements decisions
L6  gatepolicy reviews inputs basechoice
L7  evidence resultidentity artifacts
L8  staleness
L9  verify
L10 progress
L11 acceptance callers
L12 gates
L13 required doctor orchestrator
L14 events drive sync commands
L15 changes
L16 completion
L17 stacks
L18 delivery stackactions results cycletime
L19 agentsettings
L20 settings
L21 setupbase appinspect
L22 agents readiness previews
L23 setuptasks
L24 setupguide
L25 phases
L26 phasescope
L27 refine reasoning coordinate
L28 recovery
L29 scheduler
L30 watch routes
L31 httpd
L32 board
composition: board httpd routes
retired:
new:
```

The plan wrote same-layer orderings as `gitrepo → githost`; with the
convention above those become separate layers, lower first (so
`githost` imports `gitrepo`). `github.py` is split into `changes`,
`completion` and a `providers/github/` adapter and leaves the table —
done by tasks 115 (WP07) and 116 (WP08), so `githost` and `changes` are
no longer `new`. (The current table places `githost` lower than this
target, directly above `config`; see "Current layers".)
The target placed `setupguide` *below* `setuptasks`, `readiness` and
`previews`, expecting the upward imports to go by moving the shared
helpers down. They did move down — into `setupbase` and `appinspect`,
both `new` in this table until WP13 built them — but validating the
design showed the guide itself composes those three surfaces, so it
sits above them in both tables now (task 122).

## Ownership

One owner per question; `AGENTS.md` describes each module in full.

| Layer group | Modules | Owns |
| --- | --- | --- |
| stdlib leaves | `gitproc`, `records`, `redact` | the hardened git subprocess: which verbs are reads, the argv and environment a read runs with, the one `subprocess.run`; atomic and durable record writes; what a credential looks like |
| settings | `config` | paths, roots and layout problems, settings and their provenance, prompt/adapter/driver resolution, the patchable git wrappers over `gitproc` |
| shared state | `state`, `reports`, `templates` | registries and the SSE fan-out; the report clip; the launch and orchestration prompt contracts |
| work state | `taskfiles` | the only code touching `tasks/` |
| policy | `plans`, `profiles`, `reviews` | which branch, what a run may do and read, the pinned review checkout |
| evidence | `verify`, `progress`, `acceptance`, `callers` | the check runner and its records; a run's own claims; criterion decisions and findings; whether a request's claim to be a run is proved, and the Host and Origin a request names (report or enforce) |
| decisions | `gates`, `required`, `doctor` | may this merge happen; run missing required checks; readiness diagnosis |
| services | `commands`, `drive`, `events`, `sync`, `github`, `completion` | local chores; the one live drive; normalized events; ticket sync; opening and polling PRs; merge-and-clean-up (merge, publish, recover, clean up, sweep) |
| reading | `stacks`, `delivery` | the derived stack reading (edge states, path positions, the three targets), local reads only; one status per card, owning no policy |
| stack maintenance | `stackactions` | the three confirmed stack actions — retarget, update from parent, clean up after proven delivery — each bound to the identities its sheet showed |
| setup base | `setupbase`, `appinspect` | the setup decisions record, its journal, request identity and revision guards, and the agent area; the read-only inspection of the application's files |
| surfaces | `agentsettings`, `settings`, `setupguide`, `setuptasks`, `readiness`, `previews` | configuration pages and the setup guide's areas (`setupguide` composes the other three) |
| runs | `agents`, `phases`, `refine`, `reasoning`, `coordinate`, `scheduler` | headless launches; the phase beat; refinement jobs; orchestrator reasoning jobs and their validated responses; a review card's checks, independent review, repair and delegated merge; the orchestration beat |
| composition | `watch`, `routes`, `httpd`, `board` | the disk poller; every route with its request class and handler; the socket, the class doors and the body checks; startup wiring |

Standalone programs — `adapters/*/` and the `bench-verify` and
`bench-progress` shims — run as separate processes and import no core
module. The adapters' contract is `adapters/README.md`.

## Records: who writes what, under which lock

Every record and card a core module replaces goes through `records`
(task 110): a unique temporary file in the same directory, then
`os.replace`, so a reader sees the old file or the new one and two
writers never share a scratch file. `durable=True` adds the file and
directory flush. Since WP12 (task 121) that includes evidence: `evidence`
writes each record atomically, durably once its outcome is final. One
module owns each family and its read-modify-write lock; `records` owns
neither.

| Family | Owner | Lock | Durable |
| --- | --- | --- | --- |
| task cards (move, claim, PR line, sections, discussion) | `taskfiles` (`github` and `agents` for the PR line and a released claim) | `taskfiles.WRITE_LOCK` | no |
| Bench review verdicts, overrides | `gates` | `gates._LOCK` | yes |
| GitHub review observation | `gates` | `gates._LOCK` | no |
| execution plans | `plans` | the launch's own sequence | yes |
| run manifests | `profiles` | the launch's own sequence | yes |
| review checkout ownership | `reviews` | the launch's own sequence | no |
| delivered record, completion recovery record | `completion` | the card's completion claim | yes |
| PR snapshot | `github` | the PR poll | no |
| verification evidence | `evidence` (`verify` decides when) | the run's reservation, `state.LOCK` | yes, once final |
| reuse records | `evidence` (`verify._record_reuse` decides) | `state.LOCK`, the same hold as the reuse decision | no |
| a clipped check log | `verify` | the run's own end | no |
| source checkout ownership (one per run, task 131) | `verify` | the run's reservation: written before `git worktree add`, removed only by the run it names or a restart that proves it | yes |
| retained demonstration outputs and their records (task 133) | `artifacts` (`verify` decides when; `routes` for a withholding) | `artifacts._LOCK` | yes |
| suggestion dismissals (task 133) | `results` | the board's one writer | no |
| progress records | `progress` | `progress._LOCK` | no |
| required-check batches, review-entry record | `required` | `required._LOCK`, `_ON_REVIEW_LOCK` | no |
| refinement jobs | `refine` | the refine registry | no |
| orchestrator reasoning jobs, facts documents, retained responses (task 139) | `reasoning` | `reasoning.LOCK` | no |
| drive record, scrubbed drive log | `drive` | the one drive | no |
| preview configuration and records, scrubbed logs | `previews` | `previews.LOCK` | no |
| session identity | `state` | per session | no |
| setup decisions (portable) | `setupbase` (`setupguide`, `setuptasks`, `readiness` decide) | `setupbase.LOCK`, interprocess | yes |
| setup journal (machine-local) | `setupbase` | `setupbase.LOCK`, interprocess | no |
| execution policy document | `settings` | `settings._APPLY_LOCK` (single writer) | yes |
| agent keys in `.env`, local prompt templates | `agentsettings` | `agentsettings._APPLY_LOCK` (single writer) | no |
| operating agreement (machine-local) and its draft (task 137) | `agreements` | `agreements._LOCK`, interprocess | the agreement yes; the draft no |
| execution designation (portable), installation id | `execowner` | `execowner._LOCK`, interprocess; execution itself is the non-blocking `flock` on `execution.lock` | yes |
| orchestrated launch ledger (append-only) | `orchestrator` | append | no |
| orchestration operations: intent and receipt (task 141) | `operations` (`scheduler` and `coordinate` decide) | `operations._LOCK`, interprocess | yes |
| recovery incidents and their attempts; the circuit breaker (task 144) | `recovery` | `recovery._LOCK`, interprocess | yes |

**Durable writers** — the call sites passing `durable=True`, held equal
to the code by `tests/test_records_adoption.py`:

```records durable
agreements.apply
agreements.set_scope
artifacts.ingest
artifacts.ingest_report
artifacts.sweep
artifacts.withhold
completion._record_delivered
completion._remember
evidence.store
execowner._write_designation
execowner.ensure_installation_id
gates.override
gates.record_verdict
operations.begin
operations.update
plans.record
plans.restore
profiles.record
recovery._save
recovery.breaker_failure
recovery.breaker_reset
recovery.breaker_success
settings.apply
setupbase.write_decisions
verify._provision_source
```

Revision comparison is not compare-and-swap between processes. The setup
decisions have two writing processes — the board and
`board.py --setup-guide` — so their lock is a `records.InterprocessLock`
on a machine-local lock file, held across the read, the comparison and
the write. The policy document and the agent settings have one writing
process, the board, and a lock held across the same three steps makes it
one writer. What stays in place on purpose (append-only histories and
journals, run and command logs, exclusive creates of new cards, the
doctor's disposable fixtures, a launch's pinned context) is a reviewed
list in the same test, which may only shrink.

## Git: who runs it, and how (task 114, WP06)

Every git process core starts goes through `gitproc.run`, by way of
`config.git_run` (or `github._run` and `completion._run`, which build the
same argv and environment through `config.git_argv` and `config.git_env`). So a read
verb is hardened wherever it is asked from; a mutation inherits the
environment unchanged. `gitrepo` names the two repositories and holds the
operations more than one module needs. The rest of this section is the
inventory the card asked for: each wrapper, which repository it means,
and its timeout, exception and message contract. Distinct wording is
kept on purpose and held by `tests/test_gitrepo.py`.

**Shared primitives (`gitrepo`, L2).**

| Function | Repository | Timeout | On failure to run |
| --- | --- | --- | --- |
| `git(repo, …)` | named by the caller | 30 s default; `None` waits | never raises: 128 and the exception's text, or `timed_out` when given |
| `app_git(…)` | application | 30 s | never raises: 128 and the exception's text (what `config._app_git` was; gates, delivery and required call it) |
| `branch_exists` | application | none (as the drive, commands and act-pr always ran it) | false |
| `branch_for`, `dirty`, `worktree_mismatch` | application / a worktree | as `config`'s | `config`'s bodies, offered here as the door (below) |
| `fetch_branch` | application | the caller's (`plans`: `BOARD_FETCH_TIMEOUT`) | False |
| `worktree_add`, `worktree_remove`, `branch_delete` | application | none by default; `phases` passes 60 s | the result, never raising; the caller words it. A timeout reads as the exception's text unless `timed_out` is given; `phases` passes `"timed out"` to `worktree_add`, as its `_git` always read (`could not cut <branch>: timed out`) |
| `ensure_worktree` | application | as `worktree_add` | `problem`: `mismatch`, `no-branch`, `add-failed`; each caller keeps its sentence. `timed_out` passes through to `worktree_add`: `phases` gives `"timed out"`, so its halt reads `could not put a worktree on <branch>: timed out`; the drive, local commands and acting on a PR give none, unchanged |
| `fresh_branch_point` | application | none for reads; `BOARD_FETCH_TIMEOUT` for the fetch | a timeout or failed fetch is a note and HEAD; an `OSError` propagates, as it did from `agents` |

**Module wrappers, kept with their contracts.**

| Wrapper | Repository | Timeout | Contract |
| --- | --- | --- | --- |
| `plans._git` | application, or `cwd` | 30 s | via `gitrepo.git`; a timeout reads exactly `timed out` |
| `phases._git` | application, or a phase worktree | 60 s | via `gitrepo.git`; a timeout reads exactly `timed out` |
| `reviews._git`, `reviews._git_in` | application; a pinned review checkout | 30 s | never raises: 128 and the exception's text |
| `delivery._git_bounded` | application | the caller's | `None` on any failure to run |
| `github._run` | application (`cwd`, default the application) | 60 s default | **raises** what `subprocess.run` raises; callers catch. Opening and polling PRs, and the host merge verb |
| `completion._run` | application (`cwd`, default the application; a member's worktree for its status) | 60 s default | the same body and contract as `github._run`: the one git seam merge & clean up runs through, and the one its tests patch (WP07) |
| `sync._git` | work state | 30 s | never raises: 128 `no work-state repository`, `timed out`, or the exception's text |
| `taskfiles._git` | work state | 30 s | 128 `no work-state repository`; otherwise **raises** |
| `config.git_root`, `_remotes_of`, `gh_repo`, `_git_at` | named | 10 s | `None` / `[]` |
| `config.dirty_files` | a worktree | 30 s | a placeholder line, never `[]`, when status cannot be read |
| `config.branch_for` | application | 30 s | `task/<stem>` when git cannot answer |
| inline reads in `acceptance`, `profiles`, `settings`, `previews`, `required`, `agents.diff` | named | 10 s (`required`: 30 s) | each catches what it catches; unchanged by this card |
| `resultidentity.git` | named | 30 s | 128 and the exception's text. Was `verify._git`; since WP12 (task 121) it calls `gitproc.git_run` directly — the same argv and read environment as `config.git_run` — so the code that reads a source identity or a root class is inside the runner revision. `verify` and `staleness` use it too |

`config.branch_for`, `config.dirty_files` and `config.worktree_mismatch`
keep their bodies in `config` because `verify.py` calls them; `gitrepo`
offers them under the same names, and the callers this card routed —
`agents`, `drive`, `commands`, `plans` and `phases` — ask `gitrepo`.
Two other direct readers stay on `config` for now: `github` and
`completion` (`branch_for` through `github.branch_of`, and `completion`'s
`dirty_files` call, with the rest of their git wrappers in a later
package) and the verify train (`resultidentity`, `staleness`, `verify`:
`dirty_files`, `branch_for`, `worktree_mismatch`), whose effect on a
verdict is re-derived when freshness is read
(`resultidentity.BOUND_ELSEWHERE`). A delegation from `config` up to
`gitrepo` would be an upward import, so the bodies move with WP13.

**Raw invocations.** A list beginning `"git"` handed straight to
`subprocess` skips `gitproc` and its read hardening. Task 114 routed the
six bypass sites (`agents` ×9 calls, `drive`, `commands`, `plans`) and
the one `["git", "-C", …]` literal in `github`. What remains is a
reviewed list in `tests/test_gitrepo.py` that may only shrink (`gitproc`
itself, whose one raw call reads the filter drivers it switches off, is
the layer and is not scanned): the
doctor's disposable fixtures (`doctor._fixture_repo`,
`doctor._fixture_commits`),
which run git on repositories the doctor itself creates. Literals in
tests and docs are not invocations and are not scanned. The verify train
has no raw invocation; since WP12 its one wrapper asks `gitproc` and is
no longer a direct `config.git_*` caller.

**Direct `config.git_*` callers** outside `config`, `gitproc` and
`gitrepo` are counted per module in the same test, and the count may
only fall. `verify`'s one, the documented temporary exception, went with
WP12 (task 121).

## Representative flows

- **Start work.** `httpd` → `agents.start_agent` → `plans` (preflight,
  base, worktree) and `profiles` (grants, pinned context) → the adapter's
  `run` → hook events back through `events` → `state` → SSE.
- **Move to review.** `taskfiles.move_task` → `watch` sees the actor's
  move → `github` pushes and opens the PR; `required` asks for missing
  checks only under `BOARD_VERIFY_ON_REVIEW`.
- **A check.** `bench-verify` → `POST /api/verify` → `verify.request`
  (authorised against the run's frozen manifest) → the runner → an
  evidence record → `gates` and `delivery` read it.
- **Merge and clean up.** `httpd` → `completion.complete_task` → `gates`
  read twice against the exact head → merge (locally, or on origin
  through `github.merge_pr`), publish, confirm on the remote → cleanup →
  `taskfiles` moves the card.

## Install modes and update rules (as they are today)

- **Vendored** — the tool, `tasks/` and the application share one
  repository; the default.
- **External** — `BOARD_APP_ROOT`, `BOARD_WORK_ROOT` and
  `BOARD_WORKTREES` name separate roots; the application gets nothing
  from bench but agent-written code.
- **Source checkout** — a clone of bench itself, updated with git; when
  it develops bench, a separate operator install serves the board and
  the clone is the candidate application.

`update.sh` replaces `manager/core/` wholesale (this file included) and
the top-level files in `release-manifest`; `manager/local/`, the work
state and the task template are never touched. Python modules — every
core module and the selected provider, eagerly since task 122 — are read
once at board start, so a code update takes effect on restart and never
half-way through a running board; prompt templates and agent settings
are read fresh per launch. (Separate processes the board starts — the
adapters, the `bench-verify` and `bench-progress` shims — read their own
code when they start, as they always did.)

## Governance

`tests/test_architecture.py`, stdlib `ast` and `re` only, importing no
core module:

1. Top-level imports respect the current layers (exceptions: a reviewed
   list, empty today).
2. Function-level core imports are on a reviewed list that may only
   shrink — empty since task 122, so any one fails, downward too.
3. Cross-module private reaches are on a reviewed list that may only
   shrink — empty since task 122.
4. Every route in `routes.py`'s `ROUTES` table, and its request class,
   matches a snapshot, and no handler calls a door itself: the
   dispatcher in `httpd.py` applies the class's door before any handler
   runs (WP04). A table row the scanner cannot read is an error, not an
   omission.
5. Standalone programs import no core module; composition is imported
   only by composition.
6. GitHub vocabulary in core outside `github.py` is counted per module
   and may only shrink.
9. Every attribute a test assigns, `setattr`s or patches on a core
   module — `mod.X = …`, `setattr`, `mock.patch`, `mock.patch.object`,
   the `self.patch(X=…)` helper and literal loops over names — exists in
   that module. One computed target is counted rather than read.
10. Stdlib leaves are pure: `gitproc` imports no core module, at the top
   level or inside a function, and nothing outside the standard library
   (task 109), `records` (task 110) and `redact` (task 111).
11. `board.py` imports every core module at its top level (task 122), so
   a module added later without being loaded at startup fails rather
   than being imported lazily on some first request.

An exception's identity is file, enclosing function and what it reaches,
never a line number, so moving code does not churn it; each entry keeps
the line it was reviewed at for navigation, and failures print the
current line. The baselines live in the test as reviewed literals and
nothing regenerates them: a new exception is a reviewed edit of the
baseline **and** of the counts below, and an exception that disappears
fails until its entry is deleted and the count lowered, so the lists
only shrink. `python3 tests/test_architecture.py` prints the actual
inventory for comparison and writes nothing.

**Deferred**, because what they check does not exist yet — each is
written by the package that builds its subject, not now against
nothing: frontend import direction and offline assets (checks 7 and 8;
WP14–15). The purity of `providers/*` arrived with WP08 (task 116) and
is held by `tests/test_hosting_providers.py`: a provider imports only the
standard library, no core module and no other provider.

```architecture counts
top-level-exceptions 0
function-imports 0
private-reaches 0
dynamic-patch-targets 3
provider-vocabulary 180
routes 133
```
