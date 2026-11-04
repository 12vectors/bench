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

`AGENT_CONTEXT` is the read-only inputs bench pinned for the run — `[{"name",
"path", "source", "sha256", "access": "read-only"}]`, paths and hashes
only, never content. The prompt already names them; an adapter that can
grant reads narrowly may also use the list.

### `capabilities.json` — what you can actually enforce

Beside your `run`, optionally, a declaration in four words core
understands: `enforced`, `prefix-allowlist` (a prefix match over the
command line — narrowing, **not** a sandbox), `prompt-only`, `pending`.

    {"v": 1, "adapter": "claude", "version": "1",
     "capabilities": {"command-allowlist": "prefix-allowlist",
                      "command-denylist": "prefix-allowlist",
                      "read-only-tools": "enforced",
                      "model-selection": "enforced"},
     "unsupported": {"filesystem-sandbox": "why not, in one sentence"}}

This is not a grant and it changes no rule. It is what lets a profile's
`requires` list **refuse** a launch rather than report a guarantee the
vendor cannot make, and what lets the board show who answers for each one.
Declaring nothing is honest and costs nothing: a legacy board requires
nothing, so it runs through such an adapter unchanged — only an operator
who wrote a `requires` list gets the refusal. Claiming `enforced` for
something you merely ask for in a system prompt is the one thing that
makes the whole display a lie, so say `prompt-only`, or put it in
`unsupported` with the reason.

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
     "file"?: str, "cmd"?: str, "detail"?: str, "ok"?: bool,
     "running"?: bool, "agent"?: $BOARD_AGENT_ID, "task"?: $BOARD_TASK}

kinds: `session end idle edit read search command test check git plan
subagent web other`. `running: true` marks an in-flight action (shown as
the live line, not appended to the timeline); follow it with the completed
event. `kind: idle` = finished responding; `kind: end` = session over.
Classification happens in YOUR emitter — core never sees vendor payloads.

## Writing one

Read the two shipped adapters side by side — they are small and map the
same three intents onto very different vendor mechanisms. The essentials:

- `run`: launch your agent headlessly with permissions generated from
  `AGENT_MODE` + `AGENT_COMMANDS`; make sure the final output lands on
  stdout and the exit code passes through.
- `wire`: install your platform's observer (hook, plugin) into the
  project so sessions POST the normalized schema with the `BOARD_*` env
  forwarded.
- Events beat perfection: start with `session`/`end` plus a generic
  `command` per tool call, refine kinds later — `opencode/plugin.js`
  starts exactly that coarse on purpose.
