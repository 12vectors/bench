# What you can do now, and what a merge will need

**Card #93 — UX and copy proposal. Research only: nothing here is implemented.**
Read against `main` at `dd5e0a38867d` (Bench 0.4.7 plus one test commit).
Every claim about current behaviour cites the file and function it comes
from. The follow-up scope (section 8) is separate and needs its own
authorisation.

## 1. The problem, in one paragraph

On a board that declares a gate, a card with no implementation yet wears
the same alarm-coloured gate state as a card whose tests failed. The gate
evaluator is right to refuse a merge when there is no commit to bind
evidence to. But the page shows that refusal as the card's state, ahead of
everything the card's stage would say. The reader is told the card is
"blocked", the commit is "not resolved", and they should "Run ◔ review
PR", on a card that has no branch and no PR. It reads as broken, and it
reads as though work cannot start, when starting work is a separate
question with its own authority.

## 2. What the code does today

### 2.1 Gate evaluation (`manager/core/gates.py`)

- `head_for()` resolves the merge candidate from **local** branch refs only
  (`_tips()` is one `for-each-ref refs/heads`): `phase/<stem>` if present,
  else `task/<stem>`. It returns `{ref, commit, known}`. It does not read the
  recorded plan, the `**PR:**` line, the remote, or any evidence.
- `_check_requirement()` returns `unmet / head-unknown` for every check when
  `head.commit` is empty. Before it does, it works out from the evidence
  whether a run is live or failed, and then throws that answer away.
- `_review_requirement()` does **not** short-circuit on an unknown head. A
  `bench` policy with no verdicts yields `unmet / no-verdict`. Its remedy is
  `REMEDIES["no-verdict"]`: *"Run ◔ review PR on this card"*, whether or not
  a PR exists. GitHub policies yield `no-approval` and the same kind of
  remedy.
- `evaluate()` then reports `decision: "blocked"` with the summary
  *"`unit` and `bench` are unmet at an unknown head: bench cannot name the
  commit…"*. This is the right enforcement answer (`passable()` is false).
  It is the wrong introductory sentence for a card nobody has started.
- `board()` sends a compact reading for every card on a gated board
  (`S.state.gates`). It **drops `head.known`** and keeps only `head` (the
  commit) and `headRef`.

### 2.2 Rendering (`manager/core/board.html`)

| Function | What it does now | Effect on an unstarted card |
| --- | --- | --- |
| `gateChip()` | `blocked` → label `N unmet`, class `bad`, glyph `✕` | the card face shows `⚖ ✕ 2 unmet` in alarm. The face rules in AGENTS.md keep an unmet gate on the face, so it cannot be tucked away |
| `nextStep()` | checks `gate.decision === 'blocked'` **before** `switch (t.stage)` | the Overview's next step becomes *"you · The merge gate is not met: …"* in `bad`, replacing *"Not started. Move it to to-do when it is next."* |
| `signalsBlock()` | `Merge gate: blocked — <summary>` with `bad` | a second alarm row |
| `gateSheet()` | a `Blocked` panel in `bad` listing each blocked scope, then `gatePanel()` per scope | the dialog opens on the word *Blocked* |
| `gatePanel()` | heading `<scope> — <word>` (e.g. `acceptance — blocked`); row *"the commit it is about: not resolved (task/…)"* failure-styled | "acceptance — blocked" and a failed-looking commit row |
| `gateRow()` | `unmet` → `fail` class, `✕`, then `why` and `remedy` | each future requirement looks like a failed test |
| `requirementLabel()` | review row reads `review · bench` | a policy keyword, not a sentence |
| `gateSheet()` override | offered whenever the policy allows overrides and any scope is blocked | **an override can be recorded against no commit.** `override()` accepts `head=None`, and `_override_in_force()` then never applies it because it needs a commit. The result is a record with no effect. This is a real defect (see 8.2) |

### 2.3 Which authority answers which question

These are the authorities that already exist. The proposal adds no new one.
Every line on the card, Overview and dialog must come from the row that
owns its question.

| Question shown to a person | Authority (existing) | Cost to ask | Already in `/api/state`? |
| --- | --- | --- | --- |
| What stage is the card in, who holds it | `taskfiles.collect()` | free | yes |
| Is an agent on it now; did the last run fail | `agents` registry, `failedRun()` | free | yes |
| Where would the work start and what would its PR target | `plans.preflight()` (via `/api/plan`) | **bounded fetch** (`BOARD_FETCH_TIMEOUT`) | the compact `plans.summary()` only, and only for non-default cards |
| Was a plan recorded (has work been started on this machine) | `plans.recorded()` | free (one file) | `plans.summary().recorded` |
| What may a launch do; is the policy, capability or input refused | `profiles.manifest()` (via `/api/profile`) | resolves and hashes inputs | no (only `S.state.policy.kind`) |
| Would `start_agent` refuse (stage, phase card, ownership, an agent already running, worktree conflict, template contract) | `agents._validate`, `claim_for_launch`, `plans.require`, `_preflight_profile`, `templates.require`, **checked again at launch** | only by launching | partly: the client mirrors some of it in `launchActionFor()` |
| What does the merge candidate resolve to | `gates.head_for()` | one `for-each-ref` | `head`, `headRef` (not `known`) |
| What did Bench's checks find, against which commit | `verify.for_task()` / `verify.outcome_now()` / `verify.rollup()` | file reads | compact `S.state.verify` |
| Is there a PR; its head, CI, mergeability, review verdict | `**PR:**` line and the PR poll (`S.state.prs`) | free (poll already ran) | yes |
| Bench's review verdict and its head | `gates.verdicts_for()` | file read | no (sheet only) |
| GitHub's formal review state | `gates.github_reviews()` | memory and file | no (sheet only) |
| May the guarded merge happen | `gates.evaluate()` + `gates.require()`, **read again immediately before the merge** | as above | compact `S.state.gates` |
| What would merge & clean up do (mode, target, stacked refusal, publication) | `github.completion_facts()` (via `/api/task/completion`) | free (no fetch) | no |

Two consequences shape everything below:

1. **"Can start work" is never free.** A complete answer needs the plan
   preflight, which fetches, plus the profile resolution. Even both together
   are not the launch's own guards, which run again at launch. So the card
   face and Overview **never** say *Can start work* from cached state. Only
   the launch check sheet (#79's `profileSheet()`), which has just asked both
   resolvers, may say so. Even then it keeps today's wording, *"Configuration
   allows this launch"*, and adds that the launch checks again.
2. **"Not started" is an inference, so it needs evidence of absence, and
   stage is not that evidence.** `head.commit == null` means only that there
   is no *local* `task/<stem>` or `phase/<stem>` ref. Section 4 defines when
   the absence is complete enough to say *not started*.

## 3. Principles for the copy

1. **Split the two questions everywhere they appear.** *What you can do now*
   comes from launch, stage, agent and PR state. *Before this can merge*
   comes from the gate. The gate's answer never replaces the next action.
2. **A future requirement is not a failure.** Before an implementation
   commit exists, every requirement reads **Needed before merge**, in the
   neutral register: no colour, a `○` glyph, and the word carries the
   meaning. The alarm (`--alarm`) is reserved for something that went wrong
   or was refused (the list is in 5.3).
3. **Name the action that fits the lifecycle, or none.** Do not suggest
   ◔ review PR without a PR, ↻ rerun without a candidate, or an override for
   something that is simply in the future.
4. **Say "implementation commit", not "head".** The SHA, the ref, the gate
   scope, requirement ids, evidence ids, freshness and authority all stay
   available in a closed `<details>` called **Technical details**.
5. **Unknown is not "not started" and is not a pass.** If anything says
   work exists (section 4), an unresolved commit is an alarm with the real
   diagnostic.
6. **Enforcement is unchanged.** `evaluate()`'s `decision`, `passable()`,
   `require()`, the re-read before the merge, `--match-head-commit` and the
   freshness rules stay exactly as they are. This proposal changes
   presentation and adds display fields only.

## 4. Lifecycle of the merge candidate (new display field)

The gate already knows the head is missing. What it lacks is *why*. The
proposal is one display-only classification, computed in `gates.py` beside
`head_for()` from records it can already read. It changes no decision.

`candidate.state`, one of:

| state | when (all read without network) | meaning shown |
| --- | --- | --- |
| `present` | `head.commit` is set | there is an implementation commit; requirements are assessed against it |
| `none-yet` | no head **and** no trace of work: no recorded plan (`plans.recorded`), no worktree at the planned path, no `**PR:**` line and no PR poll entry, no evidence records for the card (`verify.for_task`), no Bench verdicts (`verdicts_for`), no review checkout records (`reviews.for_task`), no completion record, and no agent run of kind work or act-pr in this board's records | *Implementation not started.* Nothing to assess yet |
| `delivered` | no head, and a completion record or a merged PR says an earlier implementation was delivered | *An earlier implementation was delivered (merged at `<sha>`). There is no new implementation commit yet.* |
| `unavailable` | no head, but at least one trace of work exists | *Cannot assess merge requirements: implementation commit unavailable.* The traces are listed in Technical details |

`candidate.traces` lists the traces found, as words (`recorded plan`,
`PR #123`, `3 evidence records`, `Bench verdict at 1a2b3c…`, `worktree`). It
never includes paths in the compact board reading.

Stage is used **only to choose words, never to decide the state**:

- An `in-progress` card in state `none-yet` reads *"No implementation commit
  on this board yet"*, not *"not started"*. Someone may be working by hand,
  or on another machine whose branch has not been fetched. In team mode with
  another assignee, add *"held by `<name>` — their branch may not be on this
  board"*.
- A Backlog or To-do card with traces (returned or reopened) is
  `unavailable` or `delivered`, never `none-yet`.

**Known limit, stated in Technical details.** The classification reads
local refs and records only. A branch that exists only on the remote, with
no PR line and no local records, reads `none-yet`. Technical details says
*"Read from this board's local branches and records; a branch that exists
only on the remote is not seen until it is fetched."* The classification
must not fetch to find out: opening the card has to stay free.

## 5. Copy

### 5.1 Card face

The face rule from AGENTS.md stands: *anything bad or live stays on the
face*. The change is that a future requirement is not bad. So:

| Candidate and gate | Face | ⋯ tools |
| --- | --- | --- |
| `none-yet`, any gate state | **no gate chip on the face**. The stage already says the next action | `⚖ Merge: not yet` (neutral), tooltip *"Implementation not started — merge requirements apply once there is an implementation commit. Opening this runs nothing."* |
| `delivered` | no chip on the face | `⚖ Merge: not yet` with tooltip *"Earlier implementation delivered; no new implementation commit yet."* |
| `unavailable` | `⚖ ✕ can't assess` in `--alarm` | the same chip |
| `present`, only "needed" reasons (5.3) and stage below review | `⚖ 2 needed` neutral, in the tools only | the same chip |
| `present`, only "needed" reasons, stage review | `⚖ 2 needed` on the face, neutral, because it is the next action | the same chip |
| `present`, any "problem" reason | `⚖ ✕ check failed` / `⚖ ✕ changes requested` / `⚖ ✕ 2 problems` in `--alarm` | the same chip |
| `pending` | `⚖ ◌ checking` (accent) | the same chip |
| `allowed` | in the tools only: `⚖ ✓ met at 1a2b3c4` | the same chip |
| `overridden` | `⚖ ! overridden` in `--alarm` (unchanged) | the same chip |
| policy refused | `⚖ ✕ policy` in `--alarm` (unchanged) | the same chip |

The face **never** carries *Can start work*. The existing **▸ start work**
action keeps its slot and its server-side refusal. If a launch check has
been opened in this page session and its reading is still fresh (section 6),
the action's tooltip may add *"Launch check passed at 14:02; the launch
checks again."* The card's own state never grows a readiness word.

The face never says *Merge: not yet — implementation not started* as visible
text, which differs from the card's illustrative direction. The face law
says a card wears a status only when something has changed the next action,
and "not started" is what Backlog already means. The same sentence is the
tools chip's accessible name, so it is still one click away and screen
readers reach it.

### 5.2 Overview (detail view)

Change `nextStep()` so the gate cannot win over the lifecycle. Replace the
single "next" box with two labelled regions, in this order:

**What you can do now** (`aria-label="What you can do now"`). This comes
from the existing `nextStep()` cascade with the gate branch **removed**: a
completion in progress, a failed run, a running agent, open findings, fixes
to recheck, criteria, then the stage. The stage lines gain one launch-aware
clause:

- Backlog: *"Not started. Move it to to-do when it is next."* (unchanged)
- To-do: *"Queued. Move it to in-progress to commit to it, then ▸ start
  work."* (unchanged)
- In progress, nobody on it: *"Nobody is on it. Check launch requirements,
  then ▸ start work, or work it by hand."* Here *Check launch requirements*
  is a `dlink` that opens `profileSheet(task, 'work')`. If a fresh launch
  reading from this page session says it is blocked: *"Starting work would
  be refused: `<first stop>`. Fix: `<remedy>`."* in `bad`. That is the one
  place a launch blocker reaches the Overview, and it names the action.
- In progress, agent running: unchanged (*"Wren is working on it. ‖ hold in
  the tools stops it."*). No competing launch is offered. The region adds
  *"You can inspect its progress, its evidence (◫) and its branch plan (⌖)
  now."*

**Before this can merge** (`aria-label="Before this can merge"`). This is a
new block that comes only from the gate (`/api/state` compact reading plus
`candidate`), drawn only on a gated board. It is one sentence, then at most
three requirement lines, then the link **Merge requirements** (opens the
dialog):

| Case | Sentence |
| --- | --- |
| `none-yet`, Backlog or To-do | *"Implementation has not started, so there is nothing to assess yet. Before it can merge, the implementation will need: `unit` passing (full run) and review approval recorded by Bench."* |
| `none-yet`, In progress | *"No implementation commit on this board yet. Once there is one it will need: …"* |
| `delivered` | *"An earlier implementation was delivered (merged at `1a2b3c4`). A new implementation will need: …"* |
| `unavailable` | *"Cannot assess merge requirements: the implementation commit is unavailable. `<diagnostic>`"*, then **Fix:** `<remedy>` (section 7), in `bad` |
| `present`, needed only | *"For implementation commit `1a2b3c4`: 2 requirements still needed: `unit` has not run on it; no review approval recorded by Bench."* |
| `present`, problem | *"For implementation commit `1a2b3c4`: `unit` failed. Read its log in ◫ evidence, fix and commit, then run it again."* in `bad` |
| `pending` | *"For implementation commit `1a2b3c4`: `unit` is running. Nothing is decided until it finishes."* |
| `allowed` | *"Merge requirements met for implementation commit `1a2b3c4`."* Then, from `completion_facts()` when the sheet is opened: *"Merge & clean up will merge into `main` and push it,"* or the completion's refusal. The merge re-checks the commit. |
| `overridden` | *"`ron` proceeded over `unit` at `1a2b3c4`: `<reason>`. The requirement is still unmet."* in `bad` |
| ungated | the block is omitted. Signals keeps its existing *"no gate configured — nothing is required before a merge, and that is not a pass"* row |

The Signals `Merge gate` row follows the same table. It wears `bad` only
for the alarm cases.

### 5.3 Requirement wording and severity

One table drives the dialog rows, the Overview lines and the chip labels.
It is keyed on `gates.REASONS` and the new `candidate.state`. The reasons
and remedies already in `gates.py` stay as the **technical** text. This
adds the plain text and a display class.

| Reason | Plain text (row) | Class | Next action offered |
| --- | --- | --- | --- |
| *(any, candidate `none-yet` / `delivered`)* | **Needed before merge** | neutral `○` | none |
| `head-unknown` with candidate `unavailable` | **Cannot assess: implementation commit unavailable** | alarm `✕` | the diagnostic (section 7) |
| `not-run` | **Not run on this commit yet** | neutral `○` | *Run `unit`* (◫ evidence ↻), only when the candidate is `present` and the profile grants the check |
| `pending` | **Running** | accent `◌` | none |
| `failed` | **Failed** | alarm `✕` | *Read the log* (◫), then fix, commit and rerun |
| `prepare-failed` | **Could not run: preparation `deps` failed** | alarm `✕` | *Read the preparation log* (◫) |
| `denied` | **Request refused before running** | alarm `✕` | *Open the launch check* (⚿) |
| `cancelled` | **Cancelled before a result** | neutral `○` | *Run again* |
| `error` | **No result: `<why>`** (timeout, restart) | alarm `✕` | *Run again* |
| `stale` | **Passed earlier; the code or check has changed since** | neutral `○` | *Run again on this commit* |
| `other-commit` | **Passed on an earlier commit (`9f8e…`)** | neutral `○` | *Run again on this commit* |
| `source-dirty` | **Passed with uncommitted changes** | neutral `○` | *Commit, then run again* |
| `source-unbound` | **Result not tied to a commit** | alarm `✕` | the record's reason |
| `no-verdict` | **No review approval recorded by Bench** | neutral `○` | see 5.5 |
| `verdict-other-head` | **Bench review approved an earlier commit (`9f8e…`)** | neutral `○` | *◔ review PR* when eligible |
| `verdict-changes` | **Bench review asked for changes** | alarm `✕` | *Open review findings*, then *↻ act on PR* when eligible |
| `no-approval` | **No GitHub approval** | neutral `○` | *Request a review on GitHub* (link to the PR) |
| `approval-other-head` | **GitHub approval is for an earlier commit** | neutral `○` | ask the reviewer again (PR link) |
| `changes-requested` | **Changes requested on GitHub by `elena`** | alarm `✕` | *Open the PR review*, then *↻ act on PR* when eligible |
| `same-account` | **GitHub cannot record this approval: same account** | amber (configuration fact) | the existing remedy, in Technical details |
| `no-separate-reviewer` | **Needs approval from a different GitHub account** | neutral `○` | PR link |
| `policy-refused` | **Merge rules do not resolve** | alarm `✕` | *Open Settings → Merge rules* |
| met | **Met for `1a2b3c4`** | calm `✓` | none |
| overridden | **Overridden by `ron` (still unmet)** | alarm `!` | none |

Requirement names: `requirementLabel()` becomes
- a check: **`unit` — full run** / **`unit` — scope `fast`** (unchanged
  meaning, plainer joiner);
- review `bench`: **Review approval recorded by Bench**;
- review `github`: **GitHub review approval**;
- review `separate-reviewer`: **GitHub approval from a different account**.

The policy keyword (`bench`, `github`, …) moves to Technical details.

### 5.4 The merge-requirements dialog (`gateSheet()`)

Title **Merge requirements** (the `⚖ gate` tail becomes `⚖ merge
requirements`). The structure, top to bottom:

1. **Status line** (`role="status"`, one sentence, from the table in 5.2):
   - `none-yet`: **Not ready to merge: implementation not started.** Then:
     *"There is no implementation commit to assess yet. These requirements
     apply before merging; they do not by themselves prevent starting
     work."* Then, when the stage allows it: *"To start: Check launch
     requirements"* (link to `profileSheet(task,'work')`). The dialog never
     says *you can start*.
   - `present` + problems: **Not ready to merge: `unit` failed on
     `1a2b3c4`.**
   - `allowed`: **Merge requirements met for implementation commit
     `1a2b3c4`.** Then *"Merging re-checks this against the current commit
     and policy."*
   - `unavailable`: **Cannot assess merge requirements: implementation
     commit unavailable.** with the diagnostic and **Fix:**.
2. **Needed before merge** or **Requirements for `1a2b3c4`**: one row per
   requirement, plain text and class from 5.3, the next action as a button
   or link only where the "Next action" column allows it.
3. **Reviews: two separate records** (kept, renamed). Bench's verdicts are
   labelled **Review approval recorded by Bench**. GitHub's formal states are
   labelled **GitHub review approval**. Comments are drawn as comments, and
   the same-account note is in amber. A sentence stays: *"Neither stands in
   for the other. A relevance check (◔ still true?) or an agent's summary is
   not a review approval."* With no PR: *"No pull request yet, so there is
   no GitHub review to read."*
4. **Overrides**: only when a record exists, or when an override is
   **eligible** (8.2: the policy allows it, there is an implementation
   commit, and something is unmet, not just pending). Never for `none-yet`,
   `delivered` or `unavailable`.
5. **Technical details** (`<details>`, closed): gate scope and what it
   guards (`decision.why`), decision word (`blocked` / `allowed` / …),
   implementation commit (full SHA) and branch (`head.ref`), whether the ref
   resolved locally (`head.known`), candidate state and traces, per
   requirement the reason slug, `why`, technical `remedy`, evidence id,
   freshness and coverage, review policy keyword and `REVIEW_AUTHORITY`,
   policy id, revision and source, *"Read at 14:02 from this board's local
   records"*.

Scopes keep their meaning, introduced in plain words:

- `acceptance`: **Card merge** (*"merge & clean up into `main`"*)
- `member`: **Merge into phase `Ship the site`** (*"automatic, by the phase
  runner"*)
- `integration`: **Phase acceptance** (*"the phase branch's own head, which
  no member's evidence covers"*)

A phase member's dialog leads with `member` and lists `integration` under a
subheading **Then, with the phase** (as `gates_payload()` already returns
both). It does not lead with the phase's state.

**Stacked cards** (`plans.summary().startBranch` or `prBase` not the
application base, as in the SAFER example on `feature/test-designer`): the
status line adds *"This card's PR targets `feature/test-designer`. Merge &
clean up only merges into `main`, so this card lands through its PR into
`feature/test-designer`; see ⌖ plan."* That is the truth from
`completion_facts().stacked` and `_assert_lands_in_the_base`, and it stops
the acceptance gate implying a merge path that does not exist.

### 5.5 Review: next action by lifecycle

Remove the unconditional *"Run ◔ review PR on this card"*. The `no-verdict`
(and `verdict-other-head`) next action is chosen from facts the page already
has:

| Facts | Next action shown |
| --- | --- |
| candidate `none-yet` | none, the row is *Needed before merge* |
| commit exists, no `**PR:**`, stage in-progress | *"Review runs on a pull request. The PR opens when the card moves to review."* |
| commit exists, no PR, stage review | *"No pull request yet: ↑ open PR, then ◔ review PR."* (↑ open PR is the card's existing action) |
| PR, stage review, no agent on the card | **◔ review PR** (the card's own action spec from `launchActionFor(task,'review')`, the same key and the same arm-then-fire) |
| PR, stage review, agent running | *"An agent is working on this card. ‖ hold it before starting a review."* |
| PR, stage not review | *"Review PR is offered on cards in review/."* |
| PR merged or closed (`reviews.freshness` → `historical`) | *"The pull request has ended; its reviews are history."* No review action |

`verdict-changes` and `changes-requested` point to **review findings**
(Reviews tab) and offer **↻ act on PR** only where `launchActionFor(task,
'act-pr')` returns an action. Otherwise they show its `why`.

## 6. Loading, unavailable and stale readings

| Surface | Loading | Could not read | Stale |
| --- | --- | --- | --- |
| Card face / tools chip | compact `S.state.gates` is already there; no loading state | a card whose gate could not be read keeps today's `policy-refused` row from `board()`'s exception path, labelled **Merge requirements could not be read** (alarm) | `board()` caches for `_TTL` (15 s) per epoch. Acceptable on the face; the dialog always re-reads |
| Overview "Before this can merge" | until `S.state` arrives: *"Reading merge requirements…"* (neutral) | as above | as above |
| Overview "What you can do now" launch clause | no launch reading → the neutral *Check launch requirements* link, never a guess | — | a launch reading is fresh for 60 s **and** only while `S.state.policy` revision, the card's stage, its assignee, the `S.state.plans` entry and the agent registry are unchanged since it was taken. Otherwise it is dropped, without a word, back to *Check launch requirements* |
| Dialog | *"Reading merge requirements…"* (unchanged text, `role="status"`) | `/api/gates` not OK → **Merge requirements could not be read: `<error>`** (alarm). It must not fall back to the compact reading as though it were current | the dialog's own reading wins. If its `head.commit` differs from the compact one, add *"The implementation commit changed while you were looking; this is the current reading."* |
| Launch check | unchanged (#79) | unchanged (*Could not be checked*) | unchanged (*Checked 14:02 against current settings*) |

Opening, expanding and reading any of these stays a GET. `/api/gates` and
`/api/task/completion` do not fetch. `/api/plan` does a bounded fetch, and
that is already the case today: the Overview must not call it
automatically, only through the *Check launch requirements* link.

## 7. Diagnostics for `unavailable`

The `head-unknown` remedy (*"There is nothing to gate yet"*) is replaced
for display by a remedy chosen from the traces:

| Trace present | Diagnostic | Fix |
| --- | --- | --- |
| recorded plan names `task/<stem>`, no local ref | *"This card's work was started on branch `task/…`, which is not in this checkout."* | *"Fetch the branch, or ⌖ replan if the work was abandoned."* |
| PR line, no local ref | *"The card has PR #123 but its branch is not in this checkout."* | *"Fetch `task/…` from the remote. Bench reads local branches only."* |
| evidence or verdicts only (reopened, branch cleaned up) and no completion record | *"Earlier evidence exists for this card, but its branch is gone."* | *"Start work again to cut a new branch; earlier evidence will not count for it."* |

`REMEDIES["head-unknown"]` itself stays as the technical remedy and is shown
in Technical details. `require()`'s refusal text for a real merge attempt is
unchanged.

## 8. Follow-up implementation scope (separately authorisable)

### 8.1 Display contract (API)

All of these are **additive**, and none changes `decision`, `passable()` or
`require()`.

1. `gates.head_for()` result is unchanged. `gates.evaluate()` adds
   `candidate: {state, traces[], why, remedy}` (section 4), computed by a new
   `gates.candidate_for(filename, head, task, records)`. It reads
   `plans.recorded`, the task's `pr`, the PR poll, `verify` records (already
   hoisted), `verdicts_for`, `reviews.for_task`, the completion record and the
   worktree's existence. Add it to `PUBLIC`.
2. `gates.board()` compact reading adds `candidate` (state only, plus the
   trace words) and `known`. It adds `unmet[].class` (`needed` | `problem`)
   from one server-side table, `gates.SEVERITY`, keyed by reason and
   candidate state. Then the page does not keep a second copy of which
   reasons are failures.
3. `gates.REASONS` and `REMEDIES` stay. Add `gates.PLAIN`, the plain row text
   from 5.3, and send it in `gates_payload()` beside `reasons`. Wording is
   then in one place, testable in Python.
4. `gates_payload()` adds `completion: github.completion_facts(...)` only
   when the decision is `allowed`. It is read-only and needs no fetch.

### 8.2 Behaviour fixes found while investigating

- **No override against no commit.** `gates.override()` should refuse when
  `decision["head"]["commit"]` is empty: *"there is no implementation commit
  to override at, so nothing was recorded."* `gateSheet()` should offer the
  override only when the first blocked scope has a commit and an unmet
  (not pending) requirement. Today a no-op override can be recorded.
- `nextStep()`'s gate branch moves out of the cascade into the new "Before
  this can merge" block. No other next-step precedence changes.

### 8.3 Page changes

`gateChip()`, `requirementLabel()`, `gateRow()`, `gatePanel()`,
`gateSheet()`, `nextStep()`/`overviewHtml()` and `signalsBlock()` as in
section 5. Add a page-session cache of the last launch reading per
`(file, intent)` with the freshness rule in section 6. The cache is used for
wording only and is never sent with a launch.

### 8.4 Tests the follow-up should carry

- `tests/` (Python): one test per row of the section 4 table, through
  `evaluate()` on fixtures, asserting that `candidate.state` changes and
  `decision` does not. Also: `override()` refuses with no head; `PLAIN` and
  `SEVERITY` cover every `REASONS` key.
- Browser (`verify/browser`): a gated board with (a) a Backlog card with no
  traces, which has no alarm on the face, *Needed before merge* rows, no
  ◔ review PR and no override; (b) a returned Backlog card with evidence,
  which shows *Cannot assess…* in alarm; (c) a failing check on a review
  card, which shows **Failed** and a ◫ link; (d) `allowed`, which shows *Met
  for `<sha>`* with no global "ready"; (e) a 390 px viewport, where the two
  Overview regions stack and every state still has its word.
  Plus the existing assertion that opening the dialog writes nothing
  (`state/gates/`, `state/evidence/` unchanged).

**Out of scope for the follow-up as well:** any change to what is required,
how evidence binds, freshness, review policy, override eligibility beyond
8.2, or any automatic clearing of a requirement.

## 9. Copy matrix by lifecycle

*Face* is what the card shows without opening it. *Now* is the Overview's
"What you can do now". *Merge* is the Overview's "Before this can merge"
and the dialog's status line. All cases assume a gate of `unit` (full run)
and a `bench` review on `acceptance`.

| # | Situation | Face | Now | Merge |
| --- | --- | --- | --- | --- |
| 1 | Backlog, never started | no gate chip | Not started. Move it to to-do when it is next. | Not ready to merge: implementation not started. Will need `unit` passing and review approval recorded by Bench. (neutral) |
| 2 | Backlog, stacked on `feature/test-designer` (the SAFER case) | `⌖ task/…` plan chip only | Not started. Move it to to-do when it is next. | as 1, plus: lands through its PR into `feature/test-designer`, not by merge & clean up |
| 3 | To-do | no gate chip | Queued. Move it to in-progress, then ▸ start work. | as 1 |
| 4 | In progress, nobody on it, no launch reading | ▸ start work (hover) | Nobody is on it. Check launch requirements, then ▸ start work, or work it by hand. | No implementation commit on this board yet. Will need … (neutral) |
| 5 | In progress, launch check just said OK | ▸ start work; tooltip notes the check passed at 14:02 | as 4, with *Launch check passed at 14:02; the launch checks again.* | as 4 |
| 6 | In progress, launch blocked (missing required input) | ▸ start work (server refuses on click, as today) | **Starting work would be refused:** the context input 'workflow-brief' … is not there. **Fix:** … (alarm) | as 4 (neutral). The launch blocker is not mixed into the merge text |
| 7 | In progress, agent running, commit `1a2b` exists, `unit` not run | live agent line | Wren is working on it. You can inspect progress, ◫ evidence and ⌖ plan now. | For `1a2b…`: `unit` not run on this commit yet; no review approval recorded by Bench. (neutral, tools chip `⚖ 2 needed`) |
| 8 | Review, PR open, `unit` failed | `⚖ ✕ check failed` (alarm) | `unit` failed: read its log in ◫, fix, commit and rerun. | Not ready to merge: `unit` failed on `1a2b…`. |
| 9 | Review, `unit` preparation failed | `⚖ ✕ check failed` (alarm) | as 8, naming the preparation step | `unit` could not run: preparation `deps` failed. |
| 10 | Review, `unit` passed on an earlier commit | `⚖ 1 needed` (neutral, face) | Review it: ◔ review PR … (stage line) | `unit` passed on an earlier commit (`9f8e…`): run it again on `1a2b…`. |
| 11 | Review, PR open, no Bench verdict | `⚖ 1 needed` (neutral, face) | Review it: ◔ review PR, confirm acceptance, then drag to done. | No review approval recorded by Bench → **◔ review PR** |
| 12 | Review, branch but no PR | `⚖ 1 needed` | No pull request yet: ↑ open PR. | No review approval recorded by Bench. No pull request yet: ↑ open PR, then ◔ review PR. |
| 13 | Review, Bench review asked for changes | `⚖ ✕ changes requested` (alarm) | (findings drive this, as today) N open review findings … | Bench review asked for changes on `1a2b…` → Open review findings · ↻ act on PR |
| 14 | Review, GitHub changes requested by `elena` | `⚖ ✕ changes requested` | Changes were requested on the PR: ↻ act on PR, then review again. | Changes requested on GitHub by `elena` (at `9f8e…`). Another reviewer's approval does not clear it. |
| 15 | Review, everything met | tools: `⚖ ✓ met at 1a2b3c4` | Review it: … drag to done to merge & clean up. | Merge requirements met for implementation commit `1a2b3c4`. Merge & clean up will merge into `main` and push it; merging re-checks the commit. |
| 16 | Review, met, then a new commit lands | `⚖ 1 needed` / `⚖ ✕` per the new reading | stage line | For `2c3d…`: `unit` passed on an earlier commit … (the merge's own re-read would have refused the old reading) |
| 17 | Overridden | `⚖ ! overridden` (alarm) | stage line | `ron` proceeded over `unit` at `1a2b…`: `<reason>`. Still unmet. |
| 18 | Backlog, returned from review, branch still present | `⚖ 1 needed` / `⚖ ✕` per the real reading | Not started. Move it to to-do when it is next. *(the stage line, unchanged)* | assessed against the existing commit, exactly as in review. **Not** "not started" |
| 19 | Backlog, returned, branch deleted, evidence and verdict remain | `⚖ ✕ can't assess` (alarm) | stage line | Cannot assess merge requirements: earlier evidence exists but its branch is gone. Fix: start work again to cut a new branch; earlier evidence will not count. |
| 20 | Reopened from done after merge & clean up | no gate chip | stage line | An earlier implementation was delivered (merged at `1a2b…`). A new implementation will need … (neutral) |
| 21 | In progress, team mode, held by `elena`, no local branch | ▸ take over (hover) | `elena` holds it. ▸ take over is the deliberate reassignment. | No implementation commit on this board yet. `elena`'s branch may not be on this board. (neutral) |
| 22 | Phase member, in progress | `⟶ phase 2/3` | stage line | **Merge into phase `Ship the site`:** … ; then, with the phase, **Phase acceptance** (in details) |
| 23 | Phase card, members still running | `⟶ 1 of 3 merged` | Run the phase … | Phase acceptance: there is no phase branch yet (`none-yet`), or it is assessed against `phase/…`; never against a member's commit |
| 24 | Gate policy does not parse | `⚖ ✕ policy` (alarm) | stage line | Merge rules do not resolve: `<blocker>`. Fix in Settings → Merge rules. |
| 25 | Board with no gate | nothing | stage line | block omitted; Signals keeps "no gate configured … not a pass" |

## 10. Static mock-ups

Card face, case 1 (Backlog, gated board). There is no gate chip; the tools
hold it:

```
┌─────────────────────────────────────────────┐
│ #93                                         │
│ Distinguish current actions from future …   │
│ istos · Medium                         [⋯]  │
└─────────────────────────────────────────────┘
  ⋯ Tools for #93
    ⚖ Merge: not yet        ← neutral; accessible name:
                              "Merge: not yet — implementation not started"
    ◔ still true?
    ⌸ archive
```

Overview, case 4:

```
WHAT YOU CAN DO NOW
  Nobody is on it. [Check launch requirements], then ▸ start work,
  or work it by hand.

BEFORE THIS CAN MERGE
  No implementation commit on this board yet. Once there is one it will need:
    ○ unit — full run                         Needed before merge
    ○ Review approval recorded by Bench       Needed before merge
  [Merge requirements]
```

Dialog, case 1:

```
Merge requirements                                   #93 · ⚖ merge requirements
Not ready to merge: implementation not started.
There is no implementation commit to assess yet. These requirements apply
before merging; they do not by themselves prevent starting work.

NEEDED BEFORE MERGE · Card merge (merge & clean up into main)
  ○ unit — full run                           Needed before merge
  ○ Review approval recorded by Bench         Needed before merge

REVIEWS: TWO SEPARATE RECORDS
  — Review approval recorded by Bench         none yet
  — GitHub review approval                    no pull request yet
  Neither stands in for the other.

▸ Technical details
                                                        [Close — nothing was run]
```

Dialog, case 8:

```
Merge requirements                                   #93 · ⚖ merge requirements
Not ready to merge: unit failed on 1a2b3c4.

REQUIREMENTS FOR 1a2b3c4 · Card merge
  ✕ unit — full run          Failed · 2 of 41 tests      [Read the log ◫]
  ○ Review approval recorded by Bench   Not yet           ◔ review PR
▸ Technical details
```

## 11. Accessibility and narrow layouts

- The state is always a word. Colour and glyph repeat it and never carry it
  alone (the existing law). The neutral `○` rows use the body text colour,
  not `--dim`, so they keep at least 4.5:1 contrast.
- The two Overview regions are `<section>`s with headings (`h3`), so screen
  readers can navigate them. The dialog's status line is `role="status"`;
  only the `unavailable` and `policy-refused` cases use `role="alert"`.
- Technical details is a native `<details>`/`<summary>`, so it is
  keyboard-operable without script. Its open state persists through redraws
  through the existing `data-keep` mechanism.
- Below 760 px the requirement rows wrap the state under the name, not
  truncating it, and the action becomes a full-width button under the row.
- The tools chip's accessible name is the full sentence (*"Merge: not yet —
  implementation not started"*). The visible label is the short form.

## 12. How the acceptance examples are met

| Example on the card | Where this proposal meets it |
| --- | --- |
| Backlog, no branch or PR: neutral, not assessable, no false failure, no PR-review remedy, start availability not from the gate | §4 `none-yet`; §5.1 no face chip; §5.3 *Needed before merge*; §5.5 row 1; §5.2 the gate branch removed from `nextStep()`; matrix 1–3 |
| In progress: work underway, what can be inspected, no competing launch, tests or reviews may exist | §5.2 running-agent line; §4 "no implementation commit on this board yet"; matrix 7, 21 |
| Genuine launch blocker named independently | §5.2 the launch clause from a fresh launch reading only; §6 freshness; matrix 6 |
| Checks failed / not run / prep / cancelled / stale distinguished | §5.3 table; matrix 8–10, 16 |
| Review missing / changes requested | §5.5; matrix 11–14 |
| Merge eligible only from the exact-commit decision; mutations revalidate | §5.2 `allowed`; §5.4 status line; §3.6; matrix 15–16 |
| Returned Backlog / missing branch respected, unknown not turned into not-started | §4 traces; §7; matrix 18–20 |
| Bench approval and GitHub approval kept separate; scopes and overrides keep their meaning in details | §5.3 names; §5.4 items 3–5; §8.2; matrix 17, 22–23 |
| Accessible, consistent severity, narrow layouts; reading starts nothing | §5.3 classes; §11; §6 last paragraph |
