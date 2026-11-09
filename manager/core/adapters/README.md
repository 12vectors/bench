# Agent adapters

An adapter makes the task manager work with a particular coding agent.
Core never speaks any vendor's language — it launches jobs and ingests
normalized events; adapters translate at the edge. Two ship as reference
implementations: `claude/` (the default) and `opencode/`. Select with
`BOARD_AGENT_ADAPTER` in `local/.env`; a directory of the same name under
`local/adapters/` overrides the core one.

## The contract

An adapter is a directory with two executables, and may declare one file
(`capabilities.json`, below).

### `run` — execute one headless job to completion

- env in: `AGENT_PROMPT` (the full prompt), `AGENT_MODE` (the launch
  intent, below), `AGENT_COMMANDS` (the project's allowed command
  prefixes, below), `AGENT_MODEL` (optional, below), `AGENT_PROFILE` and
  `AGENT_CONTEXT` (optional, below), `AGENT_CWD`, and
  the `BOARD_*` passthrough (`BOARD_AGENT_ID`, `BOARD_TASK`,
  `BOARD_PORT`) which your event bridge must forward with every event.
- stdout is captured by the board as the job log. The prompts instruct the
  agent to end with marker lines (`NOT READY:`, `RELEVANCE REVIEW:`,
  `PR REVIEW:`, `ADDRESSED:`) — the board parses them from this output, so
  the agent's final text must reach stdout.
- exit 0 = completed; anything else = failed.
- The workflow brief the prompts point agents at is `AGENTS.md` at the
  repo root (`CLAUDE.md` beside it is only a compatibility pointer).
  Vendors that read `AGENTS.md` from the working directory's tree
  natively — opencode does, as does current Claude Code — pick it up in
  every worktree with no adapter work; `run` never needs to inject it.

### Launch intents (`AGENT_MODE`)

Core signals *intent*; every adapter maps it to its vendor's permission
mechanism. Headless runs have no human at a permission prompt, so
anything not auto-approved is denied — grant each intent exactly the
side effects its prompt demands, and never a blanket allow-everything
(the worktree is isolated, the shell is not):

- `work` — implement, test, commit in an isolated worktree. May edit
  files, run local git bookkeeping (`git add/commit/status/diff`) and
  the project's `AGENT_COMMANDS`. No push.
- `act-pr` — the work stance, plus `git push` (the PR must update),
  reading the PR's reviews and line comments (`gh pr view`, `gh pr
  diff`, `gh api`), and `git fetch`/`git merge` so a conflicted PR can
  be resolved by merging main into the branch. Resolution is additive
  only — the branch is public — so `git rebase` and the force-push
  spellings must be denied, not merely unlisted (a plain `git push`
  allow would otherwise cover them).
- `review` — read-only on the working tree: no edit tools, no commits.
  May read a PR (`gh pr view`, `gh pr diff`, read-only git) and post the
  verdict (`gh pr review`, `gh pr comment`).
- `refine` — **Update card with agent**: read the card and the
  application's code (the run starts in the application checkout, with no
  worktree) and print a proposed brief on stdout. Nothing else at all:
  withhold the vendor's edit tools **and its shell**, grant no command —
  not `AGENT_COMMANDS`, not a profile's grants, not `bench-verify` — and
  publish nothing. Bench parses the proposal, validates it and owns every
  write. Claude: `--disallowedTools Edit Write NotebookEdit Bash` and no
  allow-rule; opencode: `"edit": "deny"` and `"bash": {"*": "deny"}`.

**An intent you do not recognise must be refused, not defaulted.** Both
shipped `run` scripts exit 64 on an unknown `AGENT_MODE` before launching
anything — a script that fell through to its `work` branch would hand a
refinement implementation's stance. Core adds the matching half: an
intent newer than the original three is launched only through an adapter
whose `capabilities.json` lists it under `"intents"` (below), so an
adapter written before `refine` existed is refused it by name
(`intent-unsupported`) and keeps running the other three unchanged.

### The project's allowed commands (`AGENT_COMMANDS`)

The git/`gh` grants above are universal; which test/check commands a
project's agents run is project knowledge. It arrives as comma-separated
plain command *prefixes* — `BOARD_AGENT_COMMANDS` in `local/.env`, e.g.
`python3 -m unittest,npm test` — never in any vendor's rule syntax. Each
adapter renders them natively; both shipped rule languages are
prefix-pattern based, so the translation is mechanical:

- claude → `Bash(git commit:*)`-style allow-rules in the generated
  settings JSON (`claude/hook_settings.py`)
- opencode → `"permission": {"bash": {"*": "deny", "git commit *":
  "allow"}}` in a generated config, wildcard rules, last match wins
  (`opencode/permission_config.py`)

### The model (`AGENT_MODEL`) — optional

Absent = the vendor's own default: launch without any model argument and
let your CLI resolve it however it normally would. When set, it is an
opaque vendor-native model name — a claude alias, an opencode
`provider/model-id` — that core never validates or interprets; pass it
through untranslated (claude → `--model "$AGENT_MODEL"`, opencode → the
generated config's `model` key). Never send your vendor an empty value:
the board only sets the variable when a model is actually configured
(`BOARD_AGENT_MODEL` and its per-intent overrides in `local/.env`).

### The resolved profile (`AGENT_PROFILE` / `AGENT_CONTEXT`) — optional

`AGENT_COMMANDS` above is the whole of what an adapter has ever needed,
and it is still set to exactly what it always was. These two are
additions, and both are **narrowings**: an adapter that ignores them is
correct and keeps working, and one that reads them can only end up
granting less.

`AGENT_PROFILE` is the launch's resolved execution profile as JSON
(`core/profiles.py`):

    {"v": 1, "intent": "work", "policyId": "profile:work@…",
     "cwd": {"policy": "worktree", "path": "…"},
     "commands": [{"name": "unit-tests", "prefix": "python3 -m unittest",
                   "root": "worktree"}],
     "deny": ["push", "publish", "force-push", "rebase",
              "reshape-lifecycle"],
     "publication": {"commit": true, "push": false, "publish": false},
     "requires": []}

`commands` is the same project prefix list `AGENT_COMMANDS` carries, named
and validated — render `prefix` as you render those. `deny` is the half
worth adding: each word is a capability that must be denied outright,
rendered as rules that beat your allows (both shipped adapters map them in
`DENY_CAPABILITY_PREFIXES` / `DENY_CAPABILITY_PATTERNS`). Never widen from
it: a profile may turn a side effect off for an intent and may never turn
one on.

One rule about `commands` is worth stating on its own, because it is the
one that changed. **Render a profile's grants for every intent, `review`
included.** `AGENT_COMMANDS` stays restricted to `work` and `act-pr` —
the read-only intent never had the project's commands and must not gain
them by the back door — but a resolved profile's grants are named,
validated and deliberately selected, and one of them may be bench's own:
where a profile declares named checks, bench adds a grant for the
`bench-verify` shim that *asks* for one. A review that can ask bench to
run the tests is not a review that can commit, and both shipped adapters
show the shape: the shim gets an allow-rule, `git commit` and `git push`
get deny-rules, in the same generated document.

`AGENT_CONTEXT` is the read-only inputs bench pinned for the run — `[{"name",
"path", "source", "sha256", "access": "read-only"}]`, paths and hashes
only, never content. The prompt already names them; an adapter that can
grant reads narrowly may also use the list.

### The progress channel (`AGENT_PROGRESS` / `BOARD_PROGRESS_TOKEN`) — optional

A run reports what it has done, is doing, has left and is blocked on
through one executable, `core/bench-progress`. Bench hands it over only
to an adapter that declares `"progress-report"` in `capabilities.json`,
and only for `work`, `act-pr` and `review`: then `AGENT_PROGRESS` is the
shim's absolute path and `BOARD_PROGRESS_TOKEN` a token that belongs to
this run alone (the shim sends it; leave it in the environment). Render
`AGENT_PROGRESS` as one allow-rule — that executable and any arguments,
nothing composed from it (`progress_rules` in the claude adapter, the two
glob entries in opencode's `bash_rules`). With either variable absent,
render nothing, so the document is byte for byte what it was.

An adapter that declares nothing is handed neither variable, its prompt
carries no reporting section, and the board says *reporting unavailable*
for its runs. That is the whole fallback, and it is a correct adapter.

### What an adapter has nothing to do with: verification

A profile's named **checks** are not in `AGENT_PROFILE` and deliberately
so. Bench runs them itself, in a root it chose, with an environment it
built and no shell — see "Verification: bench runs the check" in the root
`AGENT.md` — and the only thing that reaches an adapter is the one grant
for the shim a run invokes to ask. So an adapter needs no notion of a
check, a scope, a preparation step or an outcome, and an adapter override
written before any of this existed gets exactly the environment it always
got.

### `capabilities.json` — what you can actually enforce

Beside your `run`, optionally, a declaration in four words core
understands: `enforced`, `prefix-allowlist` (a prefix match over the
command line — narrowing, **not** a sandbox), `prompt-only`, `pending`.

    {"v": 1, "adapter": "claude", "version": "1",
     "intents": ["work", "act-pr", "review", "refine"],
     "capabilities": {"command-allowlist": "prefix-allowlist",
                      "command-denylist": "prefix-allowlist",
                      "read-only-tools": "enforced",
                      "shell-withheld": "enforced",
                      "model-selection": "enforced"},
     "unsupported": {"filesystem-sandbox": "why not, in one sentence"}}

`intents` is which launch intents your `run` can launch. Omitted, you are
taken to run `work`, `act-pr` and `review` — exactly what every adapter
ran before the list existed — and nothing newer. To be handed `refine`
you must list it **and** declare `read-only-tools` and `shell-withheld`
as `enforced`: withholding the edit tools and the shell in your vendor's
own tool roster is what makes a refinement read-only, and a deny-rule a
command line could be shaped around is not the same guarantee.

This is not a grant and it changes no rule. It is what lets a profile's
`requires` list **refuse** a launch rather than report a guarantee the
vendor cannot make, and what lets the board show who answers for each one.
Declaring nothing is honest and costs nothing: a legacy board requires
nothing, so it runs through such an adapter unchanged — only an operator
who wrote a `requires` list gets the refusal. Claiming `enforced` for
something you merely ask for in a system prompt is the one thing that
makes the whole display a lie, so say `prompt-only`, or put it in
`unsupported` with the reason.

### `conformance` — letting the doctor check your translation

Also optional, also in `capabilities.json`, and this is the half that
lets bench's readiness doctor (`board.py --doctor --conformance`, or the
⚕ inspector) check that your translation is faithful **without core
learning anything about your vendor's rule syntax**. Two declarations:

    "conformance": {"describe": ["python3", "describe.py"],
                    "stubEnv": "BOARD_CLAUDE_BIN"}

- **`describe`** is a command — an argument list, run with your adapter's
  own directory as its working directory, so you name your own
  interpreter and core never guesses at one. Given a launch's environment
  (`AGENT_MODE`, `AGENT_PROMPT`, `AGENT_COMMANDS`, `AGENT_PROFILE`,
  `AGENT_CONTEXT`, `AGENT_MODEL`, `AGENT_CWD`) it prints **one JSON
  object** and launches nothing — no vendor binary, no model, no file
  written. The keys core reads:

      adapter   your adapter's name
      intent    the launch intent described
      argv      the whole argument list your vendor binary would receive
      cwd       the directory the run would start in
      allow     rules that permit, as rule text — your own syntax
      deny      rules that forbid, as rule text
      readOnly  whether your vendor's edit tools are withheld
      noShell   whether your vendor's shell is withheld — no command of
                any kind can be launched (required true for `refine`)
      context   the pinned inputs as delivered: paths and hashes

  Anything else is yours and is shown rather than interpreted. There is
  no `contextContents` key and there must never be one: an approved
  input reaches a run as a path to the read-only copy bench pinned, and
  its bytes have no business in a diagnostic.

  From it the doctor asks, per intent: did every named grant become a
  rule, with its fixed arguments intact? Is the working directory the one
  bench named? Do the pinned inputs reach the run? Can this intent ask
  bench for a named check — `review` included? Was the read-only intent
  granted a way to write? Was a refinement given any allow-rule at all,
  and are its edit tools and its shell withheld? And does any rule reach
  `git worktree`, `reset`, `checkout`, `switch`, `branch`, `update-ref`
  or `remote`, which would let a run choose a different lifecycle than
  the one bench planned?

- **`stubEnv`** names the environment variable that replaces your vendor
  binary. With it the doctor can execute your `run` end to end against a
  stub it writes, in a temporary directory it removes, and check that
  bench's prompt reaches the binary you launch and that your exit status
  is the binary's. Both shipped adapters already have such a variable for
  their own tests.

Declaring neither is honest: those rows read `skipped`, naming what you
would have to declare. What they never read is `passed`.

Two things worth saying plainly. First, if your `run` builds its argument
list in a shell and `describe` rebuilds it in another language, the two
can drift — hold them together with a test that asserts `describe`'s
`argv` is what `run` actually passes to the stub, as
`tests/test_execution_doctor.py` does for both shipped adapters. Second,
a `describe` that parses and a stub that launches prove what bench and
your adapter *pass*. They prove nothing about what your vendor enforces,
the doctor says so in those words, and the only thing that answers that
question is the separately authorised real-CLI smoke.

### `wire` — wire live-session visibility into the host project

Called by `install.py` with the project root as argv[1] (plus `--dry-run`).
Idempotently make the project's own interactive sessions report events —
however your platform allows (Claude Code: hooks in `.claude/settings.json`;
opencode: a plugin shim in `.opencode/plugin/` subscribing to its event
bus). Print a report; exit 0 on ok/fixed. If the platform has no way to
observe sessions, be a no-op with an honest message: the board still runs
headless jobs via `run`, you just lose the live play-by-play.

### Events — the normalized schema (v1)

POST to `http://127.0.0.1:$BOARD_PORT/api/events`:

    {"v": 1, "session": str, "kind": str, "summary": str,
     "id"?: str, "file"?: str, "cmd"?: str, "detail"?: str, "ok"?: bool,
     "running"?: bool, "agent"?: $BOARD_AGENT_ID, "task"?: $BOARD_TASK}

kinds: `session end idle edit read search command test check git plan
subagent web other`. `running: true` marks an in-flight action (shown as
the live line, not appended to the timeline); follow it with the completed
event. `kind: idle` = finished responding; `kind: end` = session over.
Classification happens in YOUR emitter — core never sees vendor payloads.

`id` is optional and worth sending: the raw event's stable identity
(`[A-Za-z0-9._:@-]`, up to 160), so a copy delivered twice is recorded
once, across a restart too. Make it from your vendor's own identity for
the action *and* the phase — the claude bridge sends
`PostToolUse:<tool_use_id>` — so an action's start and finish stay two
events and two edits of one file stay two edits. Once a session has sent
ids, an id-less event in it is taken for an older bridge's duplicate and
dropped, so send them on every event or on none. If your platform can
register the bridge twice for one session, stand the extra copy down at
the source as the claude bridge does (`BOARD_EMIT`, set by `run`).

The reply is `{"ok": true}`, and may carry `"remind": <sentence>` when the
run behind the event is due a progress update. A bridge that can hand
context back to the model at a tool boundary (claude: PostToolUse
`additionalContext`) may pass it on; one that cannot ignores it. Never
block, interrupt or end the run for it.

## Writing one

Read the two shipped adapters side by side — they are small and map the
same four intents onto very different vendor mechanisms. The essentials:

- `run`: launch your agent headlessly with permissions generated from
  `AGENT_MODE` + `AGENT_COMMANDS`; make sure the final output lands on
  stdout and the exit code passes through.
- `wire`: install your platform's observer (hook, plugin) into the
  project so sessions POST the normalized schema with the `BOARD_*` env
  forwarded.
- Events beat perfection: start with `session`/`end` plus a generic
  `command` per tool call, refine kinds later — `opencode/plugin.js`
  starts exactly that coarse on purpose.
