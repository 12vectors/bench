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
L2  state reports templates
L3  taskfiles
L4  plans profiles
L5  reviews
L6  verify
L7  progress
L8  acceptance callers
L9  gates
L10 required
L11 doctor
L12 commands drive events sync
L13 github
L14 delivery
L15 agentsettings
L16 settings
L17 setupguide
L18 agents
L19 setuptasks
L20 readiness
L21 previews
L22 phases
L23 refine
L24 routes watch
L25 httpd
L26 board
composition: board httpd routes watch
```

This is the order `AGENTS.md` states, with two facts made explicit:
`github` imports `drive`, so the old `events / github / drive / sync`
tier is split with `github` above it; and `commands`, which the old map
omitted, sits with the other leaf services on `L12`. All 176 distinct
top-level core-to-core import edges obey it.

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
deferred into functions. The test lists all **26 function-level core
imports**; thirteen of them point *up* the table and are the real
violations:

| Importer (layer) | Imports (layer), from inside | Why it matters |
| --- | --- | --- |
| `drive` (L12) | `previews` (L21), in `_watch`, `_preview_ended`, `_remember`, `_scrub_log` | a leaf service calls back into a surface |
| `plans` (L4) | `agents` (L18), in `_resolve_ref` | for `_fresh_branch_point`; WP06 moves it to a lower git module |
| `settings` (L16) | `agents` (L18), in `frozen_runs` | reads the run registry |
| `setupguide` (L17) | `readiness`, `previews`, `setuptasks` (L19–L21), in six functions | the guide composes the three modules that are built on it |
| `setuptasks` (L19) | `previews` (L21), in `gaps` | the same cycle, the other way |

The other thirteen are deferred *downward* imports (`readiness → verify`,
`setuptasks → agents`, `board → previews` …) that could be top-level;
they are counted so the list can only shrink. (There were fourteen:
`previews.scrub → verify` went with task 111, when `previews` began
asking `redact` instead.)

**68 private reaches** — `module._name` used from another module — are
listed the same way. The clusters, which the later packages remove:
`config._app_git` (gates, delivery, required → a public git read),
`agents._fresh_branch_point` (phases, plans), `gates._tips` /
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
retired: github
new: gitrepo githost gatepolicy evidence resultidentity staleness changes completion stacks
```

The plan wrote same-layer orderings as `gitrepo → githost`; with the
convention above those become separate layers, lower first (so
`githost` imports `gitrepo`). `github.py` is split into `changes`,
`completion` and a `providers/github/` adapter and leaves the table.
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
| services | `commands`, `drive`, `events`, `sync`, `github` | local chores; the one live drive; normalized events; ticket sync; PRs, polling and merge-and-clean-up |
| reading | `delivery` | one status per card, owning no policy |
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
| delivered record, completion recovery record | `github` | the card's completion claim | yes |
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
gates.override
gates.record_verdict
github._record_delivered
github._remember
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
- **Merge and clean up.** `httpd` → `github` completion → `gates` read
  twice against the exact head → merge, publish, confirm on the remote →
  cleanup → `taskfiles` moves the card.

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
nothing: purity of `providers/*`
(WP08); frontend import direction and offline assets (checks 7 and 8; WP14–15).

```architecture counts
top-level-exceptions 0
function-imports 26
private-reaches 68
dynamic-patch-targets 1
provider-vocabulary 196
routes 91
```
