# 94 — A simpler evidence-to-merge workflow

**Card #94: design and implementation plan. Nothing here is implemented.**
This document is read against `main` at `ba8338d43e44`. Implementation needs
its own approval, and section 15 is its bounded scope. Creating this
document changed no configuration, evidence, gate, card, agent or SAFER
file. Only this document and one fixture module that the suite does not
collect (`tests/repro_94_evidence_to_merge.py`) were added.

The goal is that a person on the common path only has to answer three
questions:

1. **What is happening?**
2. **What do I do next?**
3. **What, if anything, prevents delivery?**

Answering them should never require understanding policy hashes, root
names, evidence IDs, gate scopes or two approval counters.

## Contents

1. Grounding: what was reported, and what was reproduced
2. The design in one page
3. One status result: the delivery status
4. Evidence identity: what invalidates a result, and what is only history
5. Reuse across implementation and review
6. Choosing the relevant result
7. Counts
8. Review and acceptance
9. Diagnostics for restricted agents
10. Who requests checks: the execution model
11. Copy matrix and mock-ups
12. Journeys, before and after, with interaction counts
13. What happens to #92 and #93
14. Migration, and what operators can do today
15. Implementation sequence and regression scenarios
16. Trade-offs and explicit unknowns
17. Acceptance criteria, mapped

---

## 1. Grounding: what was reported, and what was reproduced

### 1.1 Reported (attributed to SAFER Lead; not independently accessed)

SAFER Lead reported the following on Bench v0.4.7 for SAFER card 36. This
plan has no access to SAFER records, and it did not touch any.

- Commit `cd2b909…` had a Bench APPROVE verdict and passing full
  `safer-review` and `browser-review` checks.
- Full `safer` evidence `ev:verification@7771115fbe7c` passed at the same
  commit. It then read `stale / policy-changed` after approved mockup
  context for the next card (37) was added to the project policy. The
  application commit did not change.
- The merge requirement named `safer` (root `worktree`). The reviewer-side
  checks have different names, so they did not satisfy it.
- The headline cited cancelled evidence `ev:verification@83bbe67eaa3e`, at an
  **older** commit: *"the run that asked for it was stopped."* The user saw
  *"0 of 5 passed"*, *"source has changed"*, *acceptance not recorded*, an
  approved review and a blocked merge all at once.
- Restricted agents were given verdict and log **paths**, but could not
  read the failure details. SAFER added its own versioned wrappers that
  return card-scoped, scrubbed diagnostics. That is a downstream
  workaround, not a requirement for every project.

### 1.2 Reproduced upstream on disposable fixtures

`tests/repro_94_evidence_to_merge.py` reproduces every mechanism above
against `main`, through the real `verify.request` and `gates.evaluate`,
using stub executables in a temporary directory. The suite does not
collect it, because its filename does not match `test*.py`. Run it with:

```bash
python3 -m unittest tests.repro_94_evidence_to_merge -v
```

All five cases pass on `ba8338d`. Each one asserts **today's** behaviour.
The implementation flips each assertion and keeps the case as a
regression test (section 15.3).

| # | Case | Today | Cause in source |
| --- | --- | --- | --- |
| R1 | Unrelated approved context is added; the source is unchanged | The same-commit pass reads `stale / policy-changed` | `verify.freshness()` compares `policyRevision`. That is `profiles.resolve()`'s `revision`, which digests the **whole document** (`doc["revision"]`), every context selection, every grant, the adapter version and every check's revision. A check is never handed context at all: `verify.child_env()` passes no context bundle. |
| R2 | R1, plus an earlier cancelled run at an older commit | The gate's reason is `cancelled`, and it cites the **older** commit | `gates._check_requirement()` walks the records newest first. A `stale` pass is kept as `candidate` and the walk continues. The next non-pass record (the old cancellation) becomes `blocker`, and `blocker` is returned before `candidate`. The commit is never compared for non-pass records. |
| R3 | The same program, at the same commit, passes under another check name | The requirement stays `not-run` | The requirement selects records by `check == name`, and `checkRevision` includes the name. **This is correct and stays.** |
| R4 | An optional check that was never run sits beside a required check that passed | The compact `Bench checks` row reads `1 of 2 passed`, worst `not-run`, while the gate reads `allowed` | `verify.board()` counts every check the policy offers (`_offered`), whether or not it is required. |
| R5 | A card with no implementation commit, on a board that allows overrides | An override record is written with `head: null`, and it can never apply | `gates.override()` does not refuse an empty `decision["head"]["commit"]`. `_override_in_force()` needs a commit. (This is #93 §8.2.) |

Two more facts from reading the source shape the design:

- **Ignored files are not part of the source identity.**
  `verify.source_identity()` hashes the commit, the tracked diff and
  untracked files that are *not* ignored (`--exclude-standard`). So a
  `worktree` run can depend on ignored state (`node_modules`, build
  caches, a virtualenv) that a fresh `source` or `review` checkout does not
  have. This is why the execution root is part of what a result is about
  (section 4.2).
- **The check's environment is inherited, not recorded.**
  `verify.child_env()` copies the board's environment, minus a denylist.
  The record keeps the names it removed, not an identity for what
  remained. A changed environment variable today changes nothing in the
  record.

## 2. The design in one page

1. **One server-side status result per card**, called the *delivery
   status* (section 3). It is computed in one place from the existing
   authorities. The card face, the Overview and the merge-requirements
   dialog all draw it, and none of them re-derives it. It gives one status
   word, one sentence, one primary action (when there is one), and the
   remaining merge requirements as supporting detail.
2. **Lifecycle first, then actionable problems, then future
   requirements.** Work in flight and genuine launch refusals come first.
   A real failure on the *current* commit can drive the next action.
   Normal future requirements are listed, not alarmed (section 3.3).
3. **A result is invalidated only by what it depends on.** Each check
   gets a *result identity* built from the source, the check's own
   definition, its coverage, its programs, its preparation, its declared
   inputs, its root class and its environment. The policy revision becomes
   provenance (history), not an invalidator (section 4). Authorisation is
   still checked separately at every action.
4. **Relevance before precedence.** The gate first fixes the commit it
   would merge and the requirement's current definition. Only then does it
   weigh the records that match both. An older commit's cancellation is
   history and is never today's reason (section 6).
5. **Reuse is proven, never named.** Implementation-time evidence can
   satisfy a reviewer-side requirement only when its result identity is
   equal. Names never make two checks equivalent (section 5).
6. **Headline counts are required obligations only** (section 7).
7. **Review assesses acceptance by default.** Per-criterion sign-off is
   an explicit opt-in merge requirement, not a default ceremony
   (section 8).
8. **Restricted agents get bounded, scrubbed, card-scoped diagnostics**
   through `bench-verify` itself (section 9).
9. **Nobody new gets permission to run anything.** The work run asks for
   required checks after its last commit, as today. A person has one
   **Run required checks** button for the current commit. Automatic
   scheduling is an opt-in operator setting, never a default
   (section 10).
10. **Enforcement is not weakened.** `gates.require()` still runs at the
    mutation, the merge still takes the SHA the gate read
    (`--match-head-commit`), and overrides are still explicit and bound to
    one commit. A display cache is never authorisation.

## 3. One status result: the delivery status

### 3.1 Where it lives

A new module, `manager/core/delivery.py`, placed in the module map after
`github` and before `agentsettings`:

```
… → gates → doctor → events / github / drive / sync → delivery → agentsettings → …
```

It owns **no policy and no enforcement**. It reads the same authorities
#93 §2.3 maps, plus the agent registry in `state`:

| Question | Authority it calls (existing) |
| --- | --- |
| Stage, assignee, PR line, phase membership | `taskfiles` |
| Agent running; last run failed | `state.AGENTS`, the failed-run record `agents` already writes |
| Completion in flight; partial (not published) | `state.COMPLETING`, the record under `state/completions/` |
| Recorded plan; stacked target | `plans.recorded()`, `plans.summary()` (no fetch) |
| What the merge would take | `gates.head_for()` |
| Requirements and their state at that commit | `gates.evaluate()` (with the section 6 selection) |
| Evidence and its current reading | `verify.outcome_now()` through `gates` |
| PR state, CI, mergeability, head | the PR poll (`github.PR_STATE`) |
| Review verdicts; findings; criterion records | `gates.verdicts_for()`, `acceptance.summary()` |
| What merge & clean up would do | `github.completion_facts()` (no fetch) |

It never fetches, launches, runs a check, writes a record or calls
GitHub. One compact reading per card is added to `/api/state` (as
`S.state.delivery`), using the same `_TTL` and epoch caching as
`gates.board()`. A full reading is served at `GET /api/card/delivery`,
which always recomputes. The page renders both. **The page contains no
status cascade of its own.** `nextStep()`, the gate branch in it and the
stage `switch` are replaced by drawing `delivery.primary`.

Mutations do not read this result. `start_agent`, `verify.request`,
`gates.override` and `github._complete` keep their own checks, and those
checks run again at the moment of the mutation.

### 3.2 The result (v1)

```jsonc
{
  "v": 1,
  "task": "36-….md",
  "status": {
    "word": "ready-to-merge",           // one of the words in 3.4
    "label": "Ready to merge",
    "sentence": "Checks and review are complete for commit cd2b909.",
    "tone": "calm"                      // neutral | working | attention | alarm | calm | idle
  },
  "primary": {                          // null when nothing is the person's to do
    "action": "complete",               // a key the page already has a handler for
    "label": "Merge & clean up",
    "who": "you",                       // you | agent | board | nobody
    "enabled": true,
    "why": null,                        // why it is disabled, when it is
    "arm": true                         // arm-then-fire, as every costly action is today
  },
  "others": [ /* every other legitimate action, never hidden */ ],
  "candidate": {"state": "present", "commit": "cd2b909…", "ref": "task/36-…",
                "qualifier": null},
  "requirements": [                     // only applicable required obligations
    {"kind": "check", "name": "safer", "coverage": "full run",
     "state": "met", "class": "met", "plain": "Passed on cd2b909",
     "action": null, "evidence": "ev:verification@7771115fbe7c",
     "reusedFrom": null}
  ],
  "counts": {"required": 2, "met": 2},
  "history": {"older": 3, "optional": 2},   // counted apart, never in the headline
  "read": {"at": 1731234567.1, "local": true, "stale": false}
}
```

`requirements[].class` is one of `met`, `needed`, `running`, `problem` or
`cannot-assess`. It comes from a single server table, `gates.SEVERITY`
(#93 §8.1). `plain` comes from `gates.PLAIN` (#93 §5.3), with one change:
it describes current-context records only (section 6).

### 3.3 Precedence: how the one primary status is chosen

The rule is evaluated top to bottom, and the first row that applies wins.
This resolves #93 integration decision 1. Lifecycle and genuine current
problems are considered together, and a future requirement never outranks
starting work.

| # | Condition (from the authority named) | Status word | Primary action |
| --- | --- | --- | --- |
| 1 | Merge & clean up is in flight (`COMPLETING`) | `merging` | none (`who: board`) |
| 2 | A completion record says merged but not published | `not-published` | **Retry publication** (the existing drag to done, offered as a button) |
| 3 | The latest run on this stage failed | `run-failed` | **Read what happened**, then the start action again |
| 4 | An agent is running on the card | `agent-working` | none. `others` has **‖ hold**. When checks for the current commit are running, the sentence adds "checks are running" |
| 5 | A phase card is running a member | `phase-running` | none. `others` has **‖ hold** |
| 6 | Stage `backlog` or `to-do` | `not-started` / `queued`, or `returned` when the candidate is not `none-yet` (section 3.5) | the stage move. Returned cards also show their candidate's state |
| 7 | Stage `in-progress`, nobody on it, a **current-commit** problem exists (a failed check, or open review findings) | `check-failed` / `changes-requested` | **▸ start work** (the fix), with **Read the failure** in `others` |
| 8 | Stage `in-progress`, nobody on it | `ready-to-start` (no candidate) or `in-progress` (candidate present) | **▸ start work** / **▸ take over**. The launch's own refusal decides. The face never says "can start" (#93 §2.3) |
| 9 | Stage `review`, phase member | `waiting-for-phase` | none (`who: board`). The phase runner merges it |
| 10 | Stage `review`, the candidate is `unavailable` | `cannot-assess` | the #93 §7 diagnostic's action (for example **Check the remote**) |
| 11 | Stage `review`, merge rules do not resolve | `rules-invalid` | **Open Settings → Merge rules** |
| 12 | Stage `review`, a current-commit check **failed** or **could not run** (`prepare-failed`, `denied`, `error`), and/or a review asked for changes at the current commit, or findings are open | `check-failed` / `cannot-run-check` / `changes-requested` | **↻ act on PR** when it is eligible; otherwise **Move back to in-progress**. When both a check and a review are failing, the sentence names both and the one action fixes both |
| 13 | Stage `review`, a required check is running at the current commit | `checks-running` | none. `others` has **Cancel** |
| 14 | Stage `review`, a required check is missing, stale or cancelled at the current commit | `checks-needed` | **Run required checks** (section 10.2) |
| 15 | Stage `review`, review is required and not met at the current commit | `review-needed` | **↑ open PR** if there is no PR; otherwise **◔ review PR** (GitHub policies: **Open the PR**) |
| 16 | Stage `review`, sign-off is configured (section 8.3) and pending | `signoff-needed` | **Confirm criteria** |
| 17 | Stage `review`, the PR was merged on GitHub outside the board | `merged-elsewhere` | **Finish: clean up** (the existing completion path, which reconciles) |
| 18 | Stage `review`, every requirement is met or overridden, and `completion_facts()` allows it | `ready-to-merge` (or `ready-overridden`, in alarm) | **Merge & clean up**. For a stacked card: *Lands through its PR into `<target>`*, with **Open the PR** |
| 19 | Stage `review`, ungated board | `review` | **◔ review PR** if there is no verdict yet, otherwise **Merge & clean up**. Both are in `others` either way |
| 20 | Stage `done` | `delivered` | none |

Notes on the rules:

- **Rows 7 and 12 only fire for the current commit.** That means the
  candidate is `present`, and the record is relevant under section 6. A
  failure at another commit is history (R2).
- **Row 14 never fires in `in-progress`.** There, a missing check is a
  future requirement, listed under *Merge requirements* and not
  alarmed. This is the replacement face rule (section 3.6).
- **Row 15 runs after row 14 only for the default sequence.** When a
  review already exists while checks are missing, row 14 still wins,
  because the review stays valid. A person may run the review first: the
  **◔ review PR** action stays in `others` and is never disabled because
  checks are missing. *A preferred next action is guidance, not an
  execution order.*
- **"Ready to merge" is action-specific.** It means: the merge rules are
  satisfied for commit X, and the completion facts permit merge & clean
  up. It is not a global "approved" claim, and the sentence always names
  the commit.
- **A PR that is already merged never produces `review-needed`** (row 17
  comes before row 18, and row 15 is skipped for `historical` PRs). This
  resolves #93 decision 2.

### 3.4 Status words

There are 22 words, one per row, each with fixed copy. The word always
carries the meaning, and colour only repeats it.

| Word | Label | Tone |
| --- | --- | --- |
| `not-started` | Not started | neutral |
| `queued` | Queued | neutral |
| `returned` | Returned — earlier work exists | neutral, or alarm if `unavailable` |
| `ready-to-start` | Ready to start | neutral |
| `in-progress` | In progress | neutral |
| `agent-working` | `<Name>` is working | working |
| `phase-running` | Phase running | working |
| `run-failed` | Run failed | alarm |
| `check-failed` | Check failed | alarm |
| `cannot-run-check` | Check could not run | alarm |
| `changes-requested` | Changes requested | alarm |
| `checks-running` | Checks running | working |
| `checks-needed` | Checks needed | neutral |
| `review-needed` | Review needed | neutral |
| `signoff-needed` | Sign-off needed | neutral |
| `waiting-for-phase` | Waiting for phase | neutral |
| `cannot-assess` | Can't assess | alarm |
| `rules-invalid` | Merge rules invalid | alarm |
| `ready-to-merge` | Ready to merge | calm |
| `ready-overridden` | Ready — overridden | alarm |
| `merging` / `not-published` / `merged-elsewhere` / `delivered` / `review` | as named | working / alarm / neutral / idle / neutral |

`cancelled`, `stale` and `not-run` are **reasons**, not statuses. They
all lead to `checks-needed`, and the sentence carries the specific
reason: *"`safer` was cancelled before a result on this commit."*,
*"`safer`'s preparation `deps` changed since it passed."*, *"`safer` has
not run on this commit."*. That way they are honest without being forced
into "failed".

### 3.5 Candidate classification that survives restart (#93 decision 4)

#93 §4's four states are kept: `present`, `none-yet`, `delivered` and
`unavailable`. They are computed only from **durable** records: the
recorded plan, the `**PR:**` line, the last PR poll snapshot (which is
persisted next to the gate state, see below), evidence records, verdicts,
review checkout records, completion records and the worktree directory.
Agent memory is used only to *add* the `agent-working` status, never to
decide a candidate state.

One qualifier is added. When the result is `none-yet` or `delivered` and
the card is held by another assignee, or the board has a remote, the
sentence is qualified: *"No implementation commit on this board yet."*
It never says *"Implementation not started"* without the phrase *"on this
board"*. The **Check the remote** action runs one bounded `git fetch` of
`task/<stem>` and `phase/<stem>` only (bounded by
`BOARD_FETCH_TIMEOUT`), and only when a person clicks it. Viewing never
fetches.

The PR poll snapshot is persisted
(`state/gates/<stem>.pr.json`: head, state, merged flag, observed-at), so
that a restart can still tell that a PR was merged (row 17) before the
first poll. It is shown as *"as of 14:02"*.

### 3.6 The face rule, changed deliberately (#93 decision 3)

The current rule in AGENTS.md (*"The card face and its tools"*) is:

> Anything bad or live stays on the face: … an unmet or overridden merge
> gate … A required check that has not run cannot be hidden this way,
> because it reads through the gate chip, and an unmet gate is always on
> the face.

It is replaced by (this text goes into AGENTS.md in implementation step 4):

> **The face shows active work and actionable current problems.** A card
> wears its delivery status only when it changes what you do next: an
> agent or check running, a failed run, a check that failed or could not
> run on the current commit, changes requested, a commit that cannot be
> assessed, merge rules that do not resolve, an override in force, not
> published, and — in `review/` — the next delivery step (`Checks needed`,
> `Review needed`, `Ready to merge`). Requirements that simply lie in the
> future (a check that has not run on an in-progress card, a review not
> yet due) are listed under **Merge requirements** in the card's tools
> and detail view, in the neutral register, never as an alarm. Nothing is
> dropped: every requirement is still listed, every one is still enforced
> at the merge, and a genuine failure is never moved off the face.

The face therefore carries at most one status pill (from
`delivery.status`). On review cards it also carries at most one footer
chip, `⚖ <n> needed` / `⚖ ✕` (from `delivery.counts`). The ◫ chip appears
on the face only for a current-commit `failed`, `cannot-run` or running
check. A stale or historical row never puts ◫ on the face.

## 4. Evidence identity: what invalidates a result, and what is only history

### 4.1 Two lists, kept apart

Each evidence record today carries one undifferentiated set of bindings.
The design splits them into two lists:

- The **result identity** (`resultId`) is everything the verdict
  *depends on*. If any part of it changes, the pass no longer holds for
  the current work, and the record reads `stale` with a specific reason.
- **Provenance** is everything that says *how the run came about*: who
  asked, under which policy revision, which adapter, which manifest and
  context, which checkout path. It is kept forever, shown in Technical
  details, and never invalidates the result.

`resultId = digest(sourceId, checkSubstance, coverage, programIds,
prepareSubstance, declaredInputIds, rootClass, envId, runnerRevision)`.

| Dimension | In `resultId`? | How it is computed | Reason shown when it moves |
| --- | --- | --- | --- |
| Source | **yes** | `sourceId` as today: the commit, the tracked diff and the non-ignored untracked files | `source-changed`: *"The code changed: it ran on `cd2b909`, the branch is now at `e41f…`."* or *"Uncommitted files changed since it ran."* |
| Check definition | **yes** | `checkSubstance` = digest(run argv, root, timeout, declared `inputs`, declared `env`). Unlike today's `checkRevision`, it **excludes the name** | `check-changed`: *"`safer`'s command changed since it passed."* |
| Coverage | **yes** | the scope name *and* its argument list | `scope-changed`: *"Scope `fast` now runs different arguments."* |
| Program | **yes** | `programId` as today: the executable, a `#!` interpreter one level deep, and absolute file arguments outside the root | `program-changed`: *"The program `/usr/bin/python3` (or its interpreter) changed."* |
| Preparation | **yes** | each step's argv, `network` flag and `programId`. Today only the step *names* are in `checkRevision`; the commands are only in the whole-profile revision | `prepare-changed`: *"Preparation `deps` changed since it passed."* |
| Declared inputs | **yes**, only for inputs the check **declares** | a new optional `inputs: ["card37-mockup", …]` on a check, naming context selections. Each `contextId` is `name@sha256` | `input-changed`: *"Input `design-system` changed since it passed."* |
| Root class | **yes** | `worktree` vs `fresh`. `source` and `review` are both `fresh`: a detached checkout of one commit that bench made, with no ignored state | not a staleness reason. It decides reuse (section 5) |
| Environment | **yes** | `envId` = digest of the **names** of every variable passed, plus a keyed hash (`HMAC-SHA256` with a machine-local key under `state/verify/env.key`) of the **values** of variables the check declares in `env`. Undeclared values are not hashed | `env-changed`: *"Environment variable `DATABASE_URL` changed since it passed."* (by name, never by value) |
| Runner | **yes** | `RUNNER_REVISION` as today | `runner-changed` (unchanged) |
| Policy revision, policy id | **no**, provenance only | as today | never a staleness reason on its own |
| Context the check did not declare | **no**, provenance only | `contextIds` as today | never a staleness reason |
| Adapter, model, manifest, requester, agent, checkout path | **no**, provenance only | as today | — |

**R1, after the change.** Adding `card37-mockup` changes the policy
revision and the manifest's context, but no dimension of `safer`'s
`resultId` moves. The pass at `cd2b909` keeps reading `passed`.

**The converse.** If a check declares `"inputs": ["card37-mockup"]` and that
file's contents change, the check reads `stale / input-changed`, with
the input's name.

### 4.2 Conservative rules for what cannot be known

The design does not claim perfect dependency discovery. Each unknown
fails closed, and the reason names a remedy instead of a vague "stale".

| Unknown | Behaviour | Remedy in the sentence |
| --- | --- | --- |
| A check reads files outside its root that it does not declare (for example, reading context by absolute path) | Not detected. Absolute **arguments** are already hashed (the program identity). Other undeclared reads are not | Documented limit: *"Bench pins what a check is given, not everything it reads."* Operators declare `inputs` when a check consumes approved context |
| Transitive dependencies (libraries, modules, services) | Not bound, as today (`PROGRAM_WHY`) | Pin them in the source (lockfiles are tracked source), or run with a `fresh` root |
| Ignored files in a `worktree` root (caches, `node_modules`) | They are part of the run, but not of `sourceId` | Reuse across root classes is refused (section 5). The recommended required route uses `source` (section 14.1) |
| Environment values a check does not declare | Names are bound; values are not | Declare `"env": ["DATABASE_URL"]` on the check to bind the value (keyed hash, never stored in the clear) |
| Secrets | Only a keyed hash of a **declared** value is stored. The key never leaves the machine, so the hash cannot be compared across machines or used for a dictionary attack elsewhere | — |
| External services (a database, an API) | Not bound | Documented: *"A pass says nothing about services the check called."* The sentence on such a check (where `network: true`) says so |
| Dirty or untracked source | The source is bound as today. A pass with dirty source never satisfies a gate (`source-dirty`), as today | *"Commit, then run checks."* |
| A record written before `resultId` existed (all evidence today) | It has no `resultId`, so it cannot prove equality | It is read with **today's** freshness rules, unchanged, including `policy-changed`. No old record is rewritten. See section 14.3 |
| The runner changed | `runner-changed`, as today | *"Bench was updated since this ran — run checks again."* |

### 4.3 Authorisation stays separate

A valid result is **evidence**, not **permission**. These rules hold
independently of reuse:

- A gate's requirements are read from the policy **in force now**
  (`profiles.gate_policy()`). If the operator adds a requirement, an old
  pass of a different check cannot meet it. If the operator changes a
  review policy, the change applies on the next read.
- Whether a check *may run* is decided by `profiles.resolve()` at each
  request, as today. A reusable historical record never authorises a
  run, and a check that is now forbidden for an intent cannot be
  requested, even if a reusable pass exists.
- A check removed from the policy cannot be met by anything, because no
  requirement names it.
- `gates.require()` is still called immediately before every guarded
  mutation, and the merge takes the SHA it read.

## 5. Reuse across implementation and review

### 5.1 The contract

Record E satisfies requirement Q at commit H **only** if all of these hold:

1. Q names check C, and C is declared in the policy now.
2. E is for check C, **or** E's `checkSubstance` equals C's current
   `checkSubstance`, **and** the operator has declared the equivalence on
   C: `"accepts": ["safer-review"]` (section 5.3). A name match alone is
   not enough. A declaration alone is not enough either: the substance
   must also be equal.
3. E's `resultId` equals the `resultId` C would have at H now, in **every**
   dimension of section 4.1 (source at H, substance, coverage, programs,
   preparation, declared inputs, root class, environment, runner).
4. E's outcome reads `passed` now, its source was bound and clean, and
   its commit is H.
5. E is the newest relevant record for Q under section 6.

When reuse applies, the requirement row reads *"Passed on `cd2b909`"* and
adds the provenance: *"run by the review of `cd2b909` (`safer-review`)."*
The row keeps E's own evidence id, requester, checkout and root. It is
never copied or re-labelled as C.

### 5.2 What refuses reuse, by design

| Difference | Result |
| --- | --- |
| A different name, with no `accepts` declaration | refused (R3 stays true) |
| `accepts` declared, but the argv, scope, programs, preparation, inputs or environment differ | refused. The row says which dimension differs: *"`safer-review` runs a different preparation, so it is not the same check as `safer`."* |
| A different root class (`worktree` against `fresh`) | refused. *"Ran in the working checkout, which can hold files a fresh checkout does not."* |
| A different commit, or dirty source | refused, as today |
| A review comment, a review verdict, an agent's progress claim, or CI | never evidence. A review verdict is only the review requirement |

Different environments and different coverage remain **distinct
assurance obligations**. A browser check and a backend check are never
equivalent, whatever they print.

### 5.3 Two routes to fewer duplicate runs

- **Same check, same root class (recommended).** Declare **one** required
  check with `root: "source"` and give both `work` and `review` the right
  to ask for it. A work run that asks for it after its last commit
  records a pass at H. A review run that asks for the same check at the
  same H gets the existing record back
  (`verify.request` → `{"outcome": "passed", "reused": "ev:…"}`): it runs
  nothing, and it writes a small reuse record naming the requester. A
  person can still force a fresh run with **Run again** (or
  `bench-verify --again <check>`), which writes a new record.
- **Two named checks, proven equal.** Where a project keeps
  `safer-review` (root `review`) for its own reasons, adding
  `"accepts": ["safer-review"]` to `safer` lets a `safer-review` record
  count **only when** its substance and root class equal `safer`'s. Both
  are `fresh` roots, so this works once `safer` uses `source`. A `review`
  root is pinned to the PR head, so the record is about H only when the
  PR head is H.

## 6. Choosing the relevant result

This replaces the walk in `gates._check_requirement()`.

### 6.1 The algorithm

```
inputs: requirement Q (check C, coverage want), candidate head H, records for this card

1. If H is unknown → return cannot-assess (candidate state from 3.5).
   Never compute a reason from records first and then discard it.
2. current_id ← resultId C would have at H now (section 4.1). If it cannot be
   computed (H is unreadable) → cannot-assess with the reason.
3. relevant ← records r where
     r.check == C, or (r.check ∈ C.accepts and r.substance == C.substance)
     and r covers or overlaps `want` (the coverage rules are unchanged)
     and r.source.commit == H
     and r.outcome != denied
4. Partition relevant:
     current   ← r.resultId == current_id
     outdated  ← r.resultId != current_id  (definition, program, input, env… changed)
     legacy    ← r has no resultId (read with today's freshness rules)
5. Decide, newest first by (requested, ended, non-pass-on-tie), using only
   records in `current` and `legacy`:
     a live record → pending (running), cite it
     the newest finished record:
        passed & covers & clean & bound → met
        failed / prepare-failed / error → problem, cite it
        cancelled                       → needed ("cancelled before a result on H")
        passed but narrow               → keep looking for a covering record; if none, needed
6. Nothing decided in step 5:
     outdated is not empty → needed, reason = the specific changed dimension of the newest
                             outdated record ("input design-system changed")
     records exist for other commits → needed, "has not run on H"; history counts them
     nothing at all → needed, "has not run on H"
```

### 6.2 What the precedence guarantees

| Situation | Today | After |
| --- | --- | --- |
| A stale pass at H, and an older cancellation at G (R2) | `cancelled`, citing G | `needed`, with the stale pass's own reason. Under 4.1, R1's pass is not stale at all, so the result is `met` |
| A pass at H, then a failure at H | the failure | the failure (the genuine newest failure is kept) |
| A pass at H, then a rerun at H still running | `pending` | `pending` (an active rerun is never hidden) |
| A failure at G, nothing at H | `failed` (the walk ignores the commit) | `needed`: *"has not run on H."* The G failure is in history, with its commit |
| A pass at G (older), nothing at H | `other-commit` | `needed`: *"Passed on G; has not run on H."* (No cherry-picking an old pass) |
| A pass at H whose check definition changed after it ran | `stale / policy-changed` | `needed`: *"`safer`'s command changed since it passed on H."* |
| A narrow pass at H, with the full run required | `not-run` + narrow note | the same |
| A denied request at H | skipped | skipped (a denial ran nothing) |
| A cancellation at H, newer than a pass at H | `cancelled` | `needed`: *"cancelled before a result on H"* (the newest relevant answer still decides, and an earlier pass is not resurrected) |
| Conflicting review findings, with a passing check | the check `met`, findings drive `nextStep` | the same, and the status is `changes-requested` (row 12) |

Unknown candidate identity never authorises a merge, because step 1 runs
first.

## 7. Counts

- **The headline count is `counts.required` and `counts.met`.** It covers
  only the obligations in `delivery.requirements`: the checks that the
  applicable gate scope requires, plus the review requirement and sign-off
  when they are configured. It is shown as `⚖ 1 needed` on review cards,
  and as *"1 of 2 merge requirements met"* in the Overview.
- **Optional checks** (registered and offered, but not required) and
  **historical runs** go into `delivery.history`. They are shown only in
  the ◫ evidence sheet, under the headings *Optional checks* and
  *Earlier runs*. They are never in a headline and never block.
- The Signals row *"Bench checks — N of M passed"* (`board.html`
  `signalsBlock`, fed by `verify.board()`) is replaced by *"Required: 1 of
  1 passed on `cd2b909`. Optional: 2 (see ◫)."* R4 then reads `1 of 1`.
- Registration still implies no obligation (as in #92 §2).

## 8. Review and acceptance

### 8.1 The default: one review that reads the acceptance criteria

The `review-pr.md` prompt already judges the diff against the task. The
default is made explicit: the PR review assesses **each acceptance
criterion and the substantive evidence together**, and lists any
criterion it could not judge. A Bench `APPROVE` at H, on a gate with
`review: "bench"`, is the review requirement. Nothing else is required by
default.

Per-criterion bookkeeping stops being a default ceremony:

- The Overview's review-stage line *"N criteria are reported done —
  confirm or send back"* (`nextStep`) and *"Review it: ◔ review PR, confirm
  acceptance, then drag to done"* go away. They prompted a ceremony that
  no gate enforced.
- The Acceptance tab, Track acceptance and every recorded decision stay
  exactly as they are. They remain available, and they are shown.
- On `done/`, *"N criteria were never accepted on record — Done is not
  acceptance"* is shown only when sign-off is configured (8.3). Without
  sign-off it is moved into the Acceptance tab as a fact.

### 8.2 Which approvals are distinct assurance

| Record | Distinct assurance? | Headline treatment |
| --- | --- | --- |
| Bench review verdict at H | yes: an agent's review of this exact commit | the review requirement when the policy is `bench` |
| GitHub formal approval at H | yes: a person, or GitHub's own reviewer, on GitHub | the review requirement when the policy is `github` |
| Approval from a separate account | yes: independence | when `separate-reviewer` |
| Copilot, comments, relevance checks, agent summaries | no | shown in Reviews, never counted |
| Bench `APPROVE` plus GitHub `APPROVED` when only one is configured | no extra assurance is required | only the configured one is counted. The other is shown as information |

Only one review requirement is counted, so a user never sees two approval
counters. Requiring both a Bench verdict and a separate GitHub reviewer
becomes possible through `review: ["bench", "separate-reviewer"]` (a list).
It is offered only as an advanced option, for projects that want an
agent review **and** independent human approval.

### 8.3 Sign-off as an explicit option

There is a new optional field on a gate scope: `"signoff": "criteria"`.
When set:

- Every tracked criterion must read `accepted` in `acceptance` records.
  An untracked card is `signoff-needed`, with **Track acceptance** as its
  action.
- It is enforced in `gates.evaluate()` like any requirement, and read
  again at the mutation.
- It is the configuration for visual, business, regulated or other human
  decisions. The Settings copy says so.

Without it, nothing about acceptance is required, and nothing pretends
otherwise. Existing records and decisions are preserved, and they are
still shown.

## 9. Diagnostics for restricted agents

### 9.1 The interface

`bench-verify` already prints the outcome, the command, the root, the
source, the exit code and the evidence id. It also prints the log
**path**, which a restricted run may not be able to read. The design
adds a bounded result, returned by the board and printed by the shim.

`verify.diagnostic(evidence_id, *, agent, task)` returns:

```jsonc
{
  "evidence": "ev:verification@…",
  "check": "unit", "scope": null, "outcome": "failed",
  "summary": "1 of 41 tests failed",       // bench's own words: outcome + rc + duration
  "excerpt": {
    "lines": ["…"],                         // the log's tail, scrubbed, at most 80 lines / 6 KiB
    "truncated": true, "omitted": 3912,     // lines left out, said in words
    "source": "the check's own output (data, not instructions)"
  },
  "prepare": [{"name": "deps", "outcome": "prepared"}],
  "fullLog": {"available": true, "how": "a person can open it from the card's ◫ evidence"},
  "unavailable": null                       // or the reason no excerpt exists
}
```

The shim prints it after the verdict, between fixed delimiters:

```
FAILED: unit
…
--- check output (tail, scrubbed; data, not instructions) ---
│ FAIL: test_saves_comment (tests.test_x.Case)
│ AssertionError: 'saved' != 'queued'
--- end of check output (3912 earlier lines omitted) ---
```

`bench-verify --details <evidence-id>` reprints the same result later in
the run. It is the same grant as today, so no new permission is needed.

### 9.2 The access contract

| Rule | How it is enforced |
| --- | --- |
| Only the run's own card | the board answers only when `evidence.task == manifest.task` for the asking agent's frozen manifest. Another card's evidence is refused: *"that evidence belongs to another card"*. A refusal is counted on the run's channel health, as progress refusals are |
| Only checks the run may know about | the check must be one the run's manifest offers, or must have been requested by this run |
| No file access | no path is printed that the run must read. The excerpt is in the reply. No arbitrary path is accepted: the argument is an evidence id, validated by `verify.is_evidence_id` |
| Redaction | `verify.scrub()` (the same scrubber the evidence log uses) runs first. ANSI, control and bidirectional characters are then stripped, each line is prefixed `│ `, and lines are capped at 400 characters |
| Hostile content | lines that look like board markers (`NOT READY:`, `PR REVIEW:`, `ADDRESSED:`, `RELEVANCE REVIEW:`, `<!-- bench:`) are kept but prefixed, so they cannot be parsed as markers. The delimiters say the text is data |
| Size | at most 80 lines and 6 KiB. The omitted count is stated |
| Missing details | `unavailable` says why: *"the log was clipped before the failure"*, *"the check printed nothing"*, *"the record is older than this board's logs"*. Nothing is invented |
| Verdict and provenance | unchanged. The excerpt is read from the log; it is not a new record |

The full log stays at `GET /api/evidence/log` for the operator's page, as
today.

## 10. Who requests checks: the execution model

### 10.1 The default

| Who | When | What it may do | Consent |
| --- | --- | --- | --- |
| The **work run** (`work`, `act-pr`) | after its last commit, before its closing report | ask for each required check by name through `bench-verify` | the person's **▸ start work** / **↻ act on PR** is the launch consent. The profile's `verify` list is the authorisation within the run. Nothing new |
| The **review run** | when the review needs evidence at the PR head | ask for checks its profile grants. An equivalent current pass is **reused** (section 5.3), not rerun | **◔ review PR** |
| A **person** | whenever the status says `checks-needed` | **Run required checks**, one armed button (10.2) | the click |
| **Nothing else** | — | a configured requirement does not schedule anything | — |

The work prompt already says "commit, then ask" where a gate is
configured. It gains one sentence: *"Ask for every required check after
your last commit; a pass before a later commit does not count."*

### 10.2 **Run required checks**

This is one armed action, available on a card whose candidate is
`present` and clean. It requests, in order, every required check whose
requirement reads `needed` at H. It goes through `verify.request` under
the `work` reading (as the ◫ sheet's ↻ does today). It skips checks that
are already running (it joins them) and checks that are met. Its
confirmation lists the checks and their side effects, in the ◫ sheet's
existing words. It replaces *N* separate ↻ clicks, and the per-check ↻
remains.

### 10.3 Retries, interruption and recovery

- **Nothing retries by itself**, as today.
- A run that is held cancels its checks (`cancelled-with-run`). The
  status becomes `checks-needed` with *"cancelled before a result on
  H"*, and **Run required checks** is the recovery.
- A board restart closes running records as `error`. They are shown the
  same way, with *"no result — the board restarted"*.
- The opt-in operator setting `BOARD_VERIFY_ON_REVIEW=1` requests missing
  required checks when a card enters `review/`, **on the board whose user
  made the move** (the actor rule), as that user's request. It is off by
  default. Settings says what it costs, and it grants no permission that
  the person's own click would not have.

## 11. Copy matrix and mock-ups

### 11.1 Fixtures and what each surface says

The gate in these fixtures is `unit` (full run, root `source`) plus a
`bench` review on `acceptance`, unless the row says otherwise. *Face* is
the pill and the face chip. *Overview* is the status block. *Dialog* is
the merge-requirements dialog's status line. The three are always drawn
from the same `delivery` result, so they cannot disagree. The browser
test (15.3, B1) asserts this for every row.

| # | Fixture | Face | Overview: status · primary action | Dialog status line |
| --- | --- | --- | --- | --- |
| F1 | Backlog, never started | no pill, no chip | Not started · *Move to to-do when it is next* | Not ready to merge: no implementation commit on this board yet. |
| F2 | To-do | no pill | Queued · *Move to in-progress, then ▸ start work* | as F1 |
| F3 | In progress, nobody on it, no commit | ▸ start work (hover) | Ready to start · **▸ start work** | as F1. The requirements are listed as *Needed before merge* |
| F4 | In progress, an agent is running, `unit` not run yet | live line | Wren is working · (none; ‖ hold in tools) | For `1a2b…`: 0 of 2 requirements met. `unit` has not run on this commit. |
| F5 | In progress, nobody on it, `unit` failed at H | `Check failed` (alarm) | Check failed: `unit` on `1a2b…` · **▸ start work** (fix) · *Read the failure* | Not ready to merge: `unit` failed on `1a2b…`. |
| F6 | In progress, a launch is refused (missing input) | ▸ start work | Ready to start · **▸ start work**. After a fresh launch check: *Starting work would be refused: …* · **Fix** | as F1 |
| F7 | Review, PR, `unit` missing at H | `Checks needed` · `⚖ 2 needed` | Checks needed: `unit` has not run on `1a2b…` · **Run required checks** | 0 of 2 met for `1a2b…`. |
| F8 | Review, `unit` running | `Checks running` | Checks running · (Cancel in tools) | `unit` is running on `1a2b…`. |
| F9 | Review, `unit` failed at H | `Check failed` | Check failed: `unit` on `1a2b…` · **↻ act on PR** · *Read the failure* | Not ready to merge: `unit` failed on `1a2b…`. |
| F10 | Review, `unit` preparation failed | `Check could not run` | `unit` could not run: preparation `deps` failed · *Read the preparation log* | as Overview |
| F11 | Review, `unit` cancelled at H | `Checks needed` | Checks needed: `unit` was cancelled before a result on `1a2b…` · **Run required checks** | as Overview |
| F12 | Review, `unit` passed, its preparation command was edited since | `Checks needed` | Checks needed: preparation `deps` changed since `unit` passed · **Run required checks** | as Overview |
| F13 | Review, checks met, no verdict, PR open | `Review needed` · `⚖ 1 needed` | Review needed · **◔ review PR** | 1 of 2 met for `1a2b…`. |
| F14 | Review, checks met, branch, no PR | `Review needed` | Review needed: no pull request yet · **↑ open PR** | as Overview |
| F15 | Review, Bench asked for changes at H | `Changes requested` | Changes requested by the Bench review of `1a2b…` · **↻ act on PR** | as Overview |
| F16 | Review, all met | `Ready to merge` | Ready to merge: checks and review complete for `1a2b…` · **Merge & clean up** | Merge requirements met for `1a2b…`. Merging checks this again. |
| F17 | F16, then a new commit is pushed | `Checks needed` | Checks needed: `unit` has not run on `2c3d…` (it passed on `1a2b…`) · **Run required checks** | 0 of 2 met for `2c3d…`. |
| F18 | Review, PR already merged on GitHub | `Merged on GitHub` | The PR was merged on GitHub · **Finish: clean up** | Delivered through the PR. Nothing to review. |
| F19 | Review, stacked on `feature/x`, all met | `Ready to merge` | Ready: lands through its PR into `feature/x` · **Open the PR** | Requirements met for `1a2b…`. This card merges into `feature/x` through its PR, not merge & clean up. |
| F20 | Phase member in review | `Waiting for phase` | Waiting for phase `Ship the site` to merge it · (none) | **Merge into phase:** … ; **Then, with the phase** (folded) |
| F21 | Phase card, members merged, integration `unit` not run | `Checks needed` | Checks needed: `unit` has not run on the phase branch `9a8b…` · **Run required checks** | Phase acceptance: assessed on `phase/…` at `9a8b…`, never on a member's commit. |
| F22 | Backlog, returned, branch present | `Returned` | Returned with work on `task/…` · *Move to in-progress to continue* | assessed against the existing commit, as in review |
| F23 | Backlog, returned, branch gone, evidence remains | `Can't assess` (alarm) | Earlier evidence exists, but its branch is not on this board · **Check the remote** | Cannot assess: implementation commit unavailable. |
| F24 | Reopened from done after merge & clean up | no pill | Not started — an earlier implementation was delivered at `1a2b…` | as Overview |
| F25 | Team mode, held by `elena`, no local branch | ▸ take over | `elena` holds it. No commit on this board yet — their branch may not be fetched · **▸ take over** · *Check the remote* | as F1, with the qualifier |
| F26 | After a restart: review, all met (records on disk) | `Ready to merge` | as F16, with *"PR state as of 14:02"* | as F16 |
| F27 | Override in force at H | `Ready — overridden` (alarm) | `ron` proceeded over `unit` at `1a2b…`: <reason>. Still unmet · **Merge & clean up** | as Overview |
| F28 | Merge rules do not parse | `Merge rules invalid` (alarm) on review cards only | Merge rules invalid · **Open Settings → Merge rules** | `<blocker>` |
| F29 | Ungated board, review, PR | no chip | Review · **◔ review PR** (then **Merge & clean up**) | No merge requirements are configured. That is not a pass. |
| F30 | SAFER36 shape (section 12.1) | `Ready to merge` | Ready to merge: checks and review complete for `cd2b909` · **Merge & clean up** | Met. `safer` passed on `cd2b909`. Policy changed since (context added); the check does not use it. (in Technical details) |
| F31 | Not published | `Not published` (alarm) | Merged locally but not on the remote: <what the remote said> · **Retry publication** | as Overview |
| F32 | In progress, failed run | `Run failed` (alarm) | The last run failed (rc=1) · *Read what happened* · **▸ start work** | as the candidate's state |

### 11.2 Mock-ups

The card face for F7, at a narrow width (300 px column):

```
┌──────────────────────────────┐
│ #42                     [⋯]  │
│ Save comments offline        │
│ istos · High                 │
│ [Checks needed]   ⚖ 2 needed │
└──────────────────────────────┘
```

The Overview for F7 (desktop):

```
CHECKS NEEDED
  unit has not run on commit 1a2b3c4.
  [ Run required checks ]          ◔ review PR · ⋯ more actions

MERGE REQUIREMENTS · 0 of 2 met for 1a2b3c4
  ○ unit — full run                  Has not run on this commit
  ○ Review by Bench                  Not yet
  ▸ Optional checks and earlier runs (3)
  ▸ Technical details
```

The Overview for F30 (SAFER36, after):

```
READY TO MERGE
  Checks and review are complete for commit cd2b909.
  [ Merge & clean up ]                       ⋯ more actions

MERGE REQUIREMENTS · 2 of 2 met for cd2b909
  ✓ safer — full run                 Passed on cd2b909
  ✓ Review by Bench                  Approved cd2b909
  ▸ Optional checks and earlier runs (4)
      cancelled · safer · 83bbe67eaa3e · older commit 5e6f…   (history)
  ▸ Technical details
      safer passed under policy rev 3f…; policy is now 9c… — changed: context
      'card37-mockup' added; safer declares no inputs, so the result holds.
```

The narrow layout (< 760 px): the status heading, the sentence, then the
primary button at full width, then *more actions* as a full-width
disclosure. Requirement rows wrap the state under the name. Nothing is
shown only on hover.

Keyboard behaviour: the primary action is the first focusable element
after the status heading. The heading is an `h3` in a
`<section aria-labelledby>`. The status sentence uses `role="status"`, and
`role="alert"` only for the alarm tones. *More actions* and the folds are
native `<details>`. The existing arm-then-fire rule applies with
Enter/Space, and the armed state is announced (*"Press again to merge"*).

## 12. Journeys, before and after, with interaction counts

A **decision** is a click or keystroke that commits the person to
something (an armed action counts as one decision, not two). A **look**
is opening a sheet only to understand the state. A **run** is a check
execution. The counts come from the tables below, one numbered line per
interaction.

### 12.1 SAFER36 (the reported shape, reproduced as R1 + R2)

**Before** (main at `ba8338d`, following the reported sequence):

| # | Interaction | Kind | Distinct assurance? |
| --- | --- | --- | --- |
| 1 | Read the face: `⚖ ✕`, `◫ stale`, "0 of 5 passed" | look | no: the counts include optional checks and history |
| 2 | Open the ⚖ dialog: the headline is a cancelled run at an older commit | look | no: wrong record |
| 3 | Open ◫: find the `stale / policy-changed` pass at the same commit | look | no |
| 4 | Work out that the policy change was unrelated next-card context | look | no |
| 5 | Rerun `safer` at the unchanged commit (↻, armed) | decision + **run** | **no**: same source, same program |
| 6 | Wait for the full suite | — | — |
| 7 | The Overview says "acceptance not recorded"; accept *k* criteria with notes | *k* decisions | no: no gate enforces it, and the review already judged them |
| 8 | Drag to done | decision | yes |
| 9 | Choose **merge & clean up** | decision | yes (the choice of outcome) |

That is **3 + k decisions, 4 looks and 1 redundant full run.** The
reviewer-side `safer-review` and `browser-review` had already passed at
`cd2b909`. Whether they duplicate `safer` is the operator's question
(section 14.2), not one Bench can answer from their names.

**After**:

| # | Interaction | Kind | Distinct assurance? |
| --- | --- | --- | --- |
| 1 | Read the face: `Ready to merge` | look (optional) | — |
| 2 | **Merge & clean up** (from the Overview, or by dragging to done) | decision | yes |
| 3 | Confirm in the completion sheet | decision | yes (this shows what will be pushed where) |

That is **2 decisions, 0 required looks and 0 runs.** The cancelled
record and the policy change are still visible in history and Technical
details.

### 12.2 An ordinary new card, on a gated board

**Before:**

| # | Interaction | Kind |
| --- | --- | --- |
| 1 | Drag to in-progress | decision |
| 2 | ▸ start work | decision |
| 3 | (The agent asks for `unit`, as today. If it did not, open ◫ and press ↻: one decision and one look) | (0–1 decision) |
| 4 | The card reaches review; the face shows `⚖ 1 needed` / `⚖ ✕` depending on the reading | look |
| 5 | ◔ review PR | decision |
| 6 | The review run asks for `unit-review` in its review root (a different name) | **run** (a duplicate, where the setup is SAFER-like) |
| 7 | The Overview says "confirm acceptance"; accept *k* criteria | *k* decisions |
| 8 | Drag to done | decision |
| 9 | Choose **merge & clean up** | decision |

That is **5 + k decisions (6 + k if the agent did not ask), 1 look and 2
runs** on a SAFER-like setup.

**After** (with the recommended setup in section 14.1):

| # | Interaction | Kind | Why it is kept |
| --- | --- | --- | --- |
| 1 | Drag to in-progress | decision | commitment to the work |
| 2 | ▸ start work | decision | launch consent (it spends tokens) |
| 3 | (If the agent did not ask: **Run required checks**) | (0–1 decision) | the person authorises a run |
| 4 | **◔ review PR**, offered as the primary action | decision | independent review of the exact commit |
| 5 | The review asks for `unit` at H and gets the existing pass back | 0 runs | — |
| 6 | **Merge & clean up** | decision | delivery |
| 7 | Confirm | decision | shows where it lands |

That is **5 decisions (6 if the agent did not ask), 0 required looks and 1
run.** With sign-off configured, add the *k* criterion decisions back:
they are then a requirement the operator chose, and they provide human
assurance that the review does not.

### 12.3 Summary

| Journey | Decisions | Required looks | Check runs |
| --- | --- | --- | --- |
| SAFER36, before | 3 + k | 4 | 1 redundant |
| SAFER36, after | 2 | 0 | 0 |
| Ordinary card, before (SAFER-like checks) | 5 + k | 1 | 2 |
| Ordinary card, after (recommended setup) | 5 | 0 | 1 |

## 13. What happens to #92 and #93

| Proposal item | Verdict | Reason |
| --- | --- | --- |
| #92 §3: remove the per-row status badge on registered checks | **retained** | Registration is not a result, and section 7 depends on keeping them apart |
| #92 §3: rewrite the `configured-untested` definition; keep the value | **retained** | — |
| #92 §2: five separate facts | **retained, extended** | Section 4.1 adds result identity and provenance as two further distinctions inside "execution evidence" |
| #92 §4: the "What to do with a registered check" fold | **revised** | "Get a fresh result" names **Run required checks** and explains reuse |
| #92 §5: no "Find a card" chooser | **retained** | — |
| #92 §3: setup guide `configured` copy | **retained** (optional) | — |
| #93 §3.6 / §8.4: "freshness unchanged; display only" | **superseded** | This card is authorised to change freshness (section 4) and selection (section 6). Enforcement points stay unchanged (section 4.3) |
| #93 §4: candidate states | **retained, revised** | They are computed from durable records only (3.5), with the "on this board" qualifier, a persisted PR snapshot and an explicit **Check the remote** action |
| #93 §5.1: the face table | **revised** | Replaced by one status pill plus at most one ⚖ chip, under the new face rule (3.6) |
| #93 §5.2: two Overview regions ("What you can do now" / "Before this can merge") | **superseded** | Two regions were two answers. Now there is one status and action, and *Merge requirements* is supporting detail (decision 1) |
| #93 §5.3: plain wording and severity per reason | **retained** | It moves server-side as `gates.PLAIN` / `gates.SEVERITY`. `cancelled`, `stale` and `other-commit` keep the neutral class |
| #93 §5.4: the dialog structure | **retained, revised** | Its status line is `delivery.status.sentence`. Technical details gains "result identity vs provenance" |
| #93 §5.5: review action by lifecycle | **retained** | It is absorbed into precedence rows 15 and 17 |
| #93 §6: freshness of the launch reading (page session) | **retained** | Used for wording only, never sent with a launch |
| #93 §7: diagnostics for `unavailable` | **retained** | — |
| #93 §8.1: additive gate fields | **revised** | `candidate` is computed by a `gates` helper and consumed by `delivery`. `unmet[].class` is retained |
| #93 §8.2: no override without a commit | **retained, as a behaviour fix** (section 15, step 1) | R5 reproduces it |
| #93 matrix row 11 ("Review it … confirm acceptance … drag to done") | **superseded** | Rows 15 and 18 offer the actual action. Acceptance is not a default ceremony (section 8) |

## 14. Migration, and what operators can do today

### 14.1 The recommended setup (the target)

```jsonc
{
  "checks": [{"name": "unit", "run": "…", "root": "source",
              "prepare": ["deps"], "timeout": 900}],
  "profiles": {"work": {"verify": ["unit"]}, "act-pr": {"verify": ["unit"]},
               "review": {"verify": ["unit"]}},
  "gates": {"acceptance": {"checks": ["unit"], "review": "bench"}}
}
```

This is one canonical required route, plus one meaningful review
requirement. Add further required checks only for **distinct coverage**
(for example a browser check, which is a different environment). Add
`signoff` only for a human decision. Add `separate-reviewer` only when
independence is the point.

### 14.2 What an operator can approve today, with no core change

These are listed for the operator's consideration only. This card
changes none of them.

| Option | Effect today | Assurance lost | Caveat |
| --- | --- | --- | --- |
| Keep project policy edits (such as new context) for between cards, or batch them | avoids R1's staleness, as a workaround | none | it only defers the problem. Every policy edit stales every pass until step 3 lands |
| Change the required check's root from `worktree` to `source` | the check runs on a fresh checkout of the exact commit, with no ignored state | none, but **compare first**: a suite that relied on worktree caches may behave differently. Run both once, on one commit, and compare | the change itself stales existing passes once (a definition change) |
| Remove a duplicate required check | fewer runs | **compare the real coverage first**: `safer-review` (root `review`) and `browser-review` may cover the PR head in an environment `safer` does not. Remove only after the comparison shows the same command, preparation and coverage | Bench cannot prove equality until step 3; this is the operator's judgement |
| Do not configure `github` review on a single-identity project | avoids the permanent `same-account` blocker | none (it could never be met) | — |

### 14.3 Compatibility rules for the implementation

- **No silent policy migration.** Nothing rewrites `profiles.json`. The
  new optional fields (`inputs`, `env`, `accepts`, `signoff`, a review
  list) do nothing until the operator writes them.
- **No rewriting of old evidence.** Records without `resultId` keep
  today's freshness rules, including `policy-changed`, until they age
  out. A new run writes the new fields. Old records stay byte for byte.
- **Gate configuration is retained.** No gate is enabled, disabled or
  relaxed by the implementation. The only change in what a gate reads is
  the relevance rule (section 6), and it only ever moves an answer from
  a stale `blocked` to a correct `needed` or `met` at the same commit.
  The table in 6.2 is the complete list.
- **No global override.** Overrides stay per card, per scope, per commit.
- **Acceptance records and opt-in policies are preserved.** Existing
  decisions are still read and shown. Sign-off is opt-in.

## 15. Implementation sequence and regression scenarios

### 15.1 Steps (one card; each step ends green and is committed)

1. **Override needs a target.** `gates.override()` refuses an empty head
   (*"there is no implementation commit to override at, so nothing was
   recorded"*). The server exposes `overrides.eligible`, and the dialog
   reads it instead of computing eligibility. This is a behaviour fix,
   independent of all copy changes. Flips R5.
2. **Relevance before precedence.** `gates._check_requirement()` follows
   section 6.1, steps 1 and 3–6, using today's freshness, with the commit
   filter and the outdated/legacy partition (legacy = everything for
   now). Flips R2. Adds rows 6.2.1–6.2.10 as tests.
3. **Result identity.** `verify` computes `checkSubstance`,
   `prepareSubstance`, `envId` and `resultId`, and stores them with
   `provenance` on every new record. `freshness()` compares `resultId`
   dimensions for records that have one, and today's rules for legacy
   records. `profiles` parses `inputs` and `env` on checks (validated
   where checks are validated, with codes `check-input-undeclared` and
   `check-env-invalid`). Flips R1.
4. **The delivery status.** `delivery.py`, `S.state.delivery`,
   `GET /api/card/delivery`. `board.html`: `nextStep()` is replaced by the
   delivery result; the face, the Overview and the dialog are drawn from
   it; the counts follow section 7; the AGENTS.md face rule follows 3.6,
   and the module map gains `delivery`. Flips R4.
5. **Run required checks and reuse.** The batch action, and
   `verify.request` returning `reused` for equal `resultId`, with a
   reuse record. `bench-verify --again`.
6. **Diagnostics.** `verify.diagnostic()`, `GET`/`POST
   /api/verify/details` (agent-scoped), `bench-verify` printing and
   `--details`.
7. **Review and acceptance.** The `review-pr.md` contract line; the
   `signoff` gate field; review lists; removing the ceremony lines from
   the default (section 8.1).
8. **Equivalence declarations.** `accepts` on checks; refusal reasons per
   dimension (5.2).
9. **Documentation.** AGENTS.md sections "The card face and its tools",
   "Gates", "Verification" and "Acceptance", updated in the same commits
   as the behaviour they describe. #92/#93 items as in section 13.

The steps are ordered so that each one is safe alone. Steps 1–3 are
backend-only and testable in Python. Step 4 is the only large UI change.
Steps 5–8 are additive.

### 15.2 Evidence needed before rollout

- `bench-backend` passing on the implementation commit, with the
  repro module's cases moved into `tests/test_evidence_to_merge.py` and
  inverted.
- `bench-browser` passing, with the B-scenarios below and screenshots at
  1280 px and 390 px.
- A disposable-fixture run of the SAFER36 shape (12.1 *after*) showing
  `Ready to merge` with zero reruns, and its converse (a declared input
  changes → `Checks needed`, naming the input).
- A dry comparison on the operator's own board, **read-only**, listing
  every card whose gate reading would change (`/api/gates` before and
  after, compared). Every change must be in the 6.2 table.

### 15.3 Executable regression scenarios

Backend (`tests/`, disposable fixtures, the `GateCase` furniture):

| Id | Scenario | Asserts |
| --- | --- | --- |
| E1 | R1: unrelated context added | the pass stays `passed`; provenance shows the policy change |
| E2 | A declared input changes | `stale / input-changed`, naming the input |
| E3 | A check's argv, scope args, preparation command, program, interpreter or declared env value changes (one subtest each) | `stale` with the specific reason |
| E4 | R2: an older-head cancellation plus a current stale pass | the reason cites H, never G |
| E5 | Rows of table 6.2 (one subtest each) | as tabulated |
| E6 | R3: a different name, no `accepts` | refused |
| E7 | `accepts` declared, equal substance, the same fresh root class at H | met, with `reusedFrom` and the original requester |
| E8 | `accepts` declared, but the root class, scope, preparation or env differs | refused, naming the dimension |
| E9 | A review comment, a verdict or a progress claim | never evidence |
| E10 | R4: optional checks plus history | the headline is required only |
| E11 | R5: override with no head | refused, nothing written |
| E12 | An override at H, then a new commit | not in force (unchanged behaviour) |
| E13 | Merge-time revalidation: a gate `allowed` at H, then a push lands before the merge | `require()` refuses; `--match-head-commit` is passed |
| E14 | A legacy record (no `resultId`) | read with today's rules, never upgraded |
| E15 | A policy that newly forbids a check for `review` | a reusable pass exists, but `verify.request` is refused for that intent |
| E16 | Diagnostics: credentials in the log, a 20,000-line log, another card's evidence id, a clipped log, marker-shaped lines | scrubbed; truncated with the count; refused; `unavailable` with a reason; prefixed |
| E17 | `delivery.status` for F1–F32 | the word, the primary action and the counts, per row |
| E18 | Restart: a PR snapshot persisted, agent memory empty | F18 and F26 hold |
| E19 | `signoff` configured: untracked, partially accepted, all accepted | `signoff-needed` ×2, then met |
| E20 | Opening the card, the dialog and `/api/card/delivery` | no file under `state/` changes; no fetch; no process started |

Browser (`verify/browser`, sandboxed container Chromium, disposable
backend):

| Id | Scenario |
| --- | --- |
| B1 | For F1, F3, F7, F9, F13, F16, F18, F23 and F30: the face pill, the Overview status and the dialog status line read the same word and the same action |
| B2 | 390 px: the status, then the primary action at full width, then more actions; every state is a word |
| B3 | Keyboard: Tab from the card to the Overview's primary action; arm-then-fire by Enter; Escape closes and returns focus |
| B4 | F4 (in progress): no alarm on the face; the requirements are in the tools only |
| B5 | Reading any surface writes nothing (file hashes are compared before and after) |

## 16. Trade-offs and explicit unknowns

**Removed operator work.** Reruns caused by unrelated policy edits;
reading an older commit's cancellation as today's reason; per-criterion
ceremony that no rule enforced; working out which of five check names
matter; two competing Overview answers.

**Retained safeguards.** Exact-commit binding; the newest relevant
failure; active reruns; definition, program and preparation changes;
review at the exact head; independent review when configured;
revalidation at the mutation; `--match-head-commit`; explicit,
attributed, single-commit overrides; fail-closed reading of legacy
records.

**Honest limits.**

- Environment and dependency equivalence is bounded. Bench binds the
  argv, the invoked programs, the declared preparation, the declared
  inputs, declared environment values and the root class. It does not
  bind libraries, services, undeclared reads or the machine. "Equal
  result identity" means *the same declared thing ran on the same code*,
  not *the same assurance in every possible world*.
- Moving policy revision to provenance means an operator edit that
  changes something a check depends on **without declaring it** no
  longer stales the pass. The remedy is to declare `inputs` or `env`.
  The policy-change note in Technical details stays visible, so the
  change is never silent.
- An agent review assessing acceptance criteria is not a human decision.
  Projects that need one configure `signoff`.
- "No implementation commit on this board" remains a local reading until
  someone presses **Check the remote**.

**Unknowns, to settle during implementation (none of them changes what
is built):**

- the exact excerpt caps (80 lines / 6 KiB are proposed; tune them from
  real logs);
- whether `BOARD_VERIFY_ON_REVIEW` is worth shipping in the first
  release, or should wait for a request;
- the HMAC key rotation story (proposed: none; a new key stales declared
  `env` bindings, which fails closed).

## 17. Acceptance criteria, mapped

| Criterion on the card | Where it is met |
| --- | --- |
| Reconciles #93's contradictions; changes the face rule in the documentation plan; the three surfaces agree | §3.3 (decision 1), §3.6, §13, §15 step 4/9, B1 |
| Restart, returned, remote-only, already-merged, stacked, phase: truthful guidance | §3.5, F18–F26, E18 |
| No override without a commit; overrides explicit and bound | §15 step 1, E11, E12, §4.3 |
| Fewer decisions, runs and ceremonies; who requests checks | §12, §10 |
| SAFER36 fixture coherent; unrelated context doesn't stale; old cancellation is history | R1/R2 → E1/E4, F30, §12.1 |
| One status across card and detail; unstarted is not failed; distinct remedies | §3.3–3.4, F1–F3, §11 |
| Staleness on relevant change only; uncertain fails closed; authorisation separate | §4.1–4.3, E2, E3, E14, E15 |
| Reuse only under the equivalence contract; provenance visible; a comment is not evidence | §5, E6–E9 |
| Current failures and reruns not hidden; old outcomes not the reason; unknown identity never authorises | §6, E4, E5 |
| Optional and history don't inflate counts; every blocker maps to a requirement and an action | §7, E10, §3.2 |
| Acceptance without mandatory bookkeeping; sign-off and independence enforceable; records preserved | §8, E19, §14.3 |
| Bounded, scrubbed, card-scoped diagnostics; hostile content handled | §9, E16 |
| Merge revalidated against the exact commit and current policy | §4.3, E13 |
| Backend and browser fixtures; narrow width and keyboard; planning changed nothing live | §15.3, B2, B3, B5; this document's header |
