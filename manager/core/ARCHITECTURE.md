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
L2  githost
L3  state reports templates gitrepo
L4  taskfiles
L5  plans profiles
L6  gatepolicy reviews
L7  evidence resultidentity
L8  staleness
L9  verify
L10 progress
L11 acceptance callers
L12 gates
L13 required
L14 doctor
L15 commands drive events sync
L16 changes
L17 completion
L18 stacks
L19 delivery
L20 agentsettings
L21 settings
L22 setupguide
L23 agents
L24 setuptasks
L25 readiness
L26 previews
L27 phases
L28 refine
L29 routes watch
L30 httpd
L31 board
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
`agentsettings` and `previews` delegate to it. `verify.py` keeps its own
table until the verify train (WP12), since any edit there changes every
recorded pass's runner identity; `doctor` and `progress` reach that table
through `verify.scrub` until then. `bootstrap.py` runs before an install
exists and keeps its copy; `tests/test_redact.py` holds the union to
every pattern of all five.

### Where the code does not obey it

The order holds at the top level only because some imports are
deferred into functions. The test lists all **25 function-level core
imports**; twelve of them point *up* the table and are the real
violations (a thirteenth, `plans → agents` in `_resolve_ref`, went with
task 114, when the branch points moved to `gitrepo` and `plans`):

| Importer (layer) | Imports (layer), from inside | Why it matters |
| --- | --- | --- |
| `drive` (L12) | `previews` (L22), in `_watch`, `_preview_ended`, `_remember`, `_scrub_log` | a leaf service calls back into a surface |
| `settings` (L17) | `agents` (L19), in `frozen_runs` | reads the run registry |
| `setupguide` (L18) | `readiness`, `previews`, `setuptasks` (L20–L22), in six functions | the guide composes the three modules that are built on it |
| `setuptasks` (L20) | `previews` (L22), in `gaps` | the same cycle, the other way |

The other thirteen are deferred *downward* imports (`readiness → verify`,
`setuptasks → agents`, `board → previews` …) that could be top-level;
they are counted so the list can only shrink. (There were fourteen:
`previews.scrub → verify` went with task 111, when `previews` began
asking `redact` instead.)

**54 private reaches** — `module._name` used from another module — are
listed the same way. (Seven went with task 114: `config._app_git` from
gates, delivery and required is `gitrepo.app_git`, and
`agents._fresh_branch_point` from phases and plans is
`gitrepo.fresh_branch_point`. Seven more went with task 120, when the
three `profiles` names settings, doctor and agentsettings reached for
became public: `profiles.digest`, `profiles.tool_relative` and
`profiles.SOURCE_WORDS`.) The clusters, which the later packages
remove: `gates._tips` /
`gates._EPOCH` / `verify._EPOCH` / `verify._KEYS` (delivery, required),
`refine` driving `agents._launch` and friends, and `readiness` and
`setuptasks` reaching 27 times into `setupguide`'s private helpers.

## Proposed layers

The maintainability plan's target (§2.2), recorded so later packages can
be checked against it when they land. Not enforced.

```architecture proposed
L0  gitproc records redact
L1  config
L2  state reports templates gitrepo
L3  githost
L4  taskfiles
L5  profiles plans
L6  gatepolicy reviews
L7  evidence resultidentity
L8  staleness
L9  verify
L10 progress
L11 acceptance callers
L12 gates
L13 required doctor
L14 events drive sync commands
L15 changes
L16 completion
L17 stacks
L18 delivery
L19 agentsettings
L20 settings
L21 setupguide
L22 agents
L23 setuptasks
L24 readiness previews
L25 phases
L26 refine
L27 watch routes
L28 httpd
L29 board
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
Note that `setupguide` stays *below* `setuptasks`, `readiness` and
`previews` in the target too: the upward imports above are removed by
moving the shared helpers down, not by reordering the table.

## Ownership

One owner per question; `AGENTS.md` describes each module in full.

| Layer group | Modules | Owns |
| --- | --- | --- |
| stdlib leaves | `gitproc`, `records`, `redact` | the hardened git subprocess: which verbs are reads, the argv and environment a read runs with, the one `subprocess.run`; atomic and durable record writes; what a credential looks like |
| settings | `config` | paths, roots and layout problems, settings and their provenance, prompt/adapter/driver resolution, the patchable git wrappers over `gitproc` |
| shared state | `state`, `reports`, `templates` | registries and the SSE fan-out; the report clip; the four launch prompt contracts |
| work state | `taskfiles` | the only code touching `tasks/` |
| policy | `plans`, `profiles`, `reviews` | which branch, what a run may do and read, the pinned review checkout |
| evidence | `verify`, `progress`, `acceptance`, `callers` | the check runner and its records; a run's own claims; criterion decisions and findings; whether a request's claim to be a run is proved, and the Host and Origin a request names (report or enforce) |
| decisions | `gates`, `required`, `doctor` | may this merge happen; run missing required checks; readiness diagnosis |
| services | `commands`, `drive`, `events`, `sync`, `github`, `completion` | local chores; the one live drive; normalized events; ticket sync; opening and polling PRs; merge-and-clean-up (merge, publish, recover, clean up, sweep) |
| reading | `stacks`, `delivery` | the derived stack reading (edge states, path positions, the three targets), local reads only; one status per card, owning no policy |
| surfaces | `agentsettings`, `settings`, `setupguide`, `setuptasks`, `readiness`, `previews` | configuration pages and the setup guide's areas |
| runs | `agents`, `phases`, `refine` | headless launches; the phase beat; refinement jobs |
| composition | `watch`, `routes`, `httpd`, `board` | the disk poller; every route with its request class and handler; the socket, the class doors and the body checks; startup wiring |

Standalone programs — `adapters/*/` and the `bench-verify` and
`bench-progress` shims — run as separate processes and import no core
module. The adapters' contract is `adapters/README.md`.

## Records: who writes what, under which lock

Every record and card a core module replaces goes through `records`
(task 110): a unique temporary file in the same directory, then
`os.replace`, so a reader sees the old file or the new one and two
writers never share a scratch file. `durable=True` adds the file and
directory flush. `verify.py` keeps its own writers until WP12. One
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
| progress records | `progress` | `progress._LOCK` | no |
| required-check batches, review-entry record | `required` | `required._LOCK`, `_ON_REVIEW_LOCK` | no |
| refinement jobs | `refine` | the refine registry | no |
| drive record, scrubbed drive log | `drive` | the one drive | no |
| preview configuration and records, scrubbed logs | `previews` | `previews.LOCK` | no |
| session identity | `state` | per session | no |
| setup decisions (portable) | `setupguide` | `setupguide.LOCK`, interprocess | yes |
| setup journal (machine-local) | `setupguide` | `setupguide.LOCK`, interprocess | no |
| execution policy document | `settings` | `settings._APPLY_LOCK` (single writer) | yes |
| agent keys in `.env`, local prompt templates | `agentsettings` | `agentsettings._APPLY_LOCK` (single writer) | no |

**Durable writers** — the call sites passing `durable=True`, held equal
to the code by `tests/test_records_adoption.py`:

```records durable
completion._record_delivered
completion._remember
evidence.store
gates.override
gates.record_verdict
plans.record
profiles.record
settings.apply
setupguide._write_decisions
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
| `verify._git` | named | 30 s | 128 and the exception's text. **Deferred to WP12**: no edit to `verify.py` outside its train |

`config.branch_for`, `config.dirty_files` and `config.worktree_mismatch`
keep their bodies in `config` because `verify.py` calls them; `gitrepo`
offers them under the same names, and the callers this card routed —
`agents`, `drive`, `commands`, `plans` and `phases` — ask `gitrepo`.
Two other direct readers stay on `config` for now: `github` and
`completion` (`branch_for` through `github.branch_of`, and `completion`'s
`dirty_files` call, with the rest of their git wrappers in a later
package) and `verify` (WP12). A delegation from `config` up to
`gitrepo` would be an upward import, so the bodies move with WP12/WP13.

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
tests and docs are not invocations and are not scanned. `verify.py` has
no raw invocation today; its own wrapper is counted with the direct
`config.git_*` callers below rather than routed, until WP12.

**Direct `config.git_*` callers** outside `config`, `gitproc` and
`gitrepo` are counted per module in the same test, and the count may
only fall — including `verify`'s one, which is the documented temporary
exception.

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
state and the task template are never touched. Python modules are read
once at board start, so a code update takes effect on restart; prompt
templates and agent settings are read fresh per launch.

## Governance

`tests/test_architecture.py`, stdlib `ast` and `re` only, importing no
core module:

1. Top-level imports respect the current layers (exceptions: a reviewed
   list, empty today).
2. Function-level core imports are on a reviewed list that may only
   shrink.
3. Cross-module private reaches are on a reviewed list that may only
   shrink.
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
function-imports 20
private-reaches 49
dynamic-patch-targets 2
provider-vocabulary 182
routes 91
```
