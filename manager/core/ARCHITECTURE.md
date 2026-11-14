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
L0  config
L1  state reports templates
L2  taskfiles
L3  plans profiles
L4  reviews
L5  verify
L6  progress
L7  acceptance
L8  gates
L9  required
L10 doctor
L11 commands drive events sync
L12 github
L13 delivery
L14 agentsettings
L15 settings
L16 setupguide
L17 agents
L18 setuptasks
L19 readiness
L20 previews
L21 phases
L22 refine
L23 httpd watch
L24 board
composition: board httpd watch
```

This is the order `AGENTS.md` states, with two facts made explicit:
`github` imports `drive`, so the old `events / github / drive / sync`
tier is split with `github` above it; and `commands`, which the old map
omitted, sits with the other leaf services on `L11`. All 175 distinct
top-level core-to-core import edges obey it.

### Where the code does not obey it

The order holds at the top level only because some imports are
deferred into functions. The test lists all **27 function-level core
imports**; thirteen of them point *up* the table and are the real
violations:

| Importer (layer) | Imports (layer), from inside | Why it matters |
| --- | --- | --- |
| `drive` (L11) | `previews` (L20), in `_watch`, `_preview_ended`, `_remember`, `_scrub_log` | a leaf service calls back into a surface |
| `plans` (L3) | `agents` (L17), in `_resolve_ref` | for `_fresh_branch_point`; WP06 moves it to a lower git module |
| `settings` (L15) | `agents` (L17), in `frozen_runs` | reads the run registry |
| `setupguide` (L16) | `readiness`, `previews`, `setuptasks` (L18–L20), in six functions | the guide composes the three modules that are built on it |
| `setuptasks` (L18) | `previews` (L20), in `gaps` | the same cycle, the other way |

The other fourteen are deferred *downward* imports (`readiness → verify`,
`setuptasks → agents`, `board → previews` …) that could be top-level;
they are counted so the list can only shrink.

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
L11 acceptance
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
new: gitproc records redact gitrepo githost gatepolicy evidence resultidentity staleness changes completion stacks routes
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
| settings | `config` | paths, roots and layout problems, settings and their provenance, prompt/adapter/driver resolution, the hardened git read |
| shared state | `state`, `reports`, `templates` | registries and the SSE fan-out; the report clip; the four launch prompt contracts |
| work state | `taskfiles` | the only code touching `tasks/` |
| policy | `plans`, `profiles`, `reviews` | which branch, what a run may do and read, the pinned review checkout |
| evidence | `verify`, `progress`, `acceptance` | the check runner and its records; a run's own claims; criterion decisions and findings |
| decisions | `gates`, `required`, `doctor` | may this merge happen; run missing required checks; readiness diagnosis |
| services | `commands`, `drive`, `events`, `sync`, `github` | local chores; the one live drive; normalized events; ticket sync; PRs, polling and merge-and-clean-up |
| reading | `delivery` | one status per card, owning no policy |
| surfaces | `agentsettings`, `settings`, `setupguide`, `setuptasks`, `readiness`, `previews` | configuration pages and the setup guide's areas |
| runs | `agents`, `phases`, `refine` | headless launches; the phase beat; refinement jobs |
| composition | `watch`, `httpd`, `board` | the disk poller; HTTP routes and the page; startup wiring |

Standalone programs — `adapters/*/` and the `bench-verify` and
`bench-progress` shims — run as separate processes and import no core
module. The adapters' contract is `adapters/README.md`.

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
4. Every route in `httpd.py`, and the door its branch calls
   (`policy-door`, `card-write-door`, or none), matches a snapshot. A
   dispatch the scanner cannot read is an error, not an omission.
5. Standalone programs import no core module; composition is imported
   only by composition.
6. GitHub vocabulary in core outside `github.py` is counted per module
   and may only shrink.
9. Every attribute a test assigns, `setattr`s or patches on a core
   module — `mod.X = …`, `setattr`, `mock.patch`, `mock.patch.object`,
   the `self.patch(X=…)` helper and literal loops over names — exists in
   that module. One computed target is counted rather than read.

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
nothing: purity of `gitproc`, `records`, `redact` and `providers/*`
(WP03, WP08); route *classes* beyond the doors that exist (WP04);
frontend import direction and offline assets (checks 7 and 8; WP14–15).

```architecture counts
top-level-exceptions 0
function-imports 27
private-reaches 68
dynamic-patch-targets 1
provider-vocabulary 195
routes 91
```
