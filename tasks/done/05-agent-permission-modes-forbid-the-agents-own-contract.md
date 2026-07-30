# 05 — Launch headless agents with permissions that allow their own contract

**Status:** Done
**Priority:** High — the work loop's core promise (implement, test, commit) is currently impossible for the agent asked to keep it
**Type:** Bug

The first real work agent (Cleo, card 02) finished "cleanly" having
committed nothing and run no tests — not through negligence but because
the permission mode it was launched under denied every `python3`
execution and every git write. Its log says so in the first line. The
adapter's launch flags contradict the prompt's own instructions.

## Context

- `manager/core/adapters/claude/run:24` — work mode launches
  `claude -p … --permission-mode acceptEdits`. Headless runs have no
  human to answer permission prompts, so anything not auto-approved is
  auto-denied. `acceptEdits` auto-approves file edits only; read-only
  bash (ls / grep / sed) evidently still runs, but `python3 -m unittest`,
  `git add`, `git commit` were all denied.
- `manager/core/prompts/work.md` — the same launch tells the agent to
  "run the relevant test suites until they pass" and "commit your work in
  clear, reviewable commits". Under these flags, both are unsatisfiable.
- Observed end-to-end consequence: the agent exits 0, the board moves the
  card to review/, and the PR machinery has an empty branch to work with
  — see the "Verification status" section Cleo appended to
  `../review/02-start-cleanly-when-bench-is-the-project.md`.
- The same audit is owed to the other two launch shapes:
  - review mode (`run:20-22`): `--permission-mode default` headless —
    the review-pr prompt requires `gh pr review --approve/--request-changes`,
    a state-changing command that default-mode headless will deny.
  - act-pr runs as work mode and must `git push` — also a write beyond
    `acceptEdits`.

## What to build

- Per launch mode, enumerate the side effects the mode's own prompt
  demands and grant exactly those — via Claude Code permission
  allow-rules (auto-approve by pattern, e.g. `Bash(git commit:*)`), not
  a coarser `--permission-mode`. The stances:
  - **work**: `acceptEdits` + allow `git add/commit/status/diff` and the
    project's test/check commands. No push.
  - **act-pr**: the work stance + `Bash(git push:*)`.
  - **review / review-pr**: read-only defaults (edit tools disallowed,
    as today) + allow exactly `gh pr review` / `gh pr comment`.
  Never `bypassPermissions`: the worktree is isolated, the shell is not.
- Deliver the rules through the file the adapter already generates:
  `hook_settings.py` builds a per-launch settings JSON for the event
  hooks, and the same settings format carries a `permissions.allow`
  list. `run` passes the mode in; one generated file carries both hooks
  and allowlist.
- The git/`gh` rules are universal; the test/check commands are project
  knowledge — default documented in `core/.env.example`, overridable in
  `local/.env`. Note the convergence with
  `03-focus-checks-come-from-the-project.md`: the checks definition it
  introduces is nearly this list, so once both land the allowlist
  should feed from it rather than being maintained twice.
- A guard against silent repeats: if the work agent exits without a
  single commit on its branch, the board should say so loudly in the
  ticker (and arguably keep the card in in-progress instead of
  advancing it) — an empty branch reaching review/ is how this bug
  stayed invisible until a human read the report's fine print.

### The opencode adapter (build it here, as the portability proof)

Don't just keep the contract vendor-neutral in theory — ship
`manager/core/adapters/opencode/` in this task, so the second adapter
exists and the claims above are load-tested against a real second
vendor:

- `run` — generate a per-launch opencode config from `AGENT_MODE` plus
  the neutral command-prefix list, mapping the same three stances:
  work → `"edit": "allow"` + bash map (`"*": "deny"`, git add/commit and
  project commands `"allow"`); act-pr → work + `"git push *": "allow"`;
  review → `"edit": "deny"` + only `gh pr review *` / `gh pr comment *`.
  Launch `opencode run` headlessly with that config, final text on
  stdout so the board's marker parsing works unchanged. Exit code
  passes through.
- `wire` — an opencode plugin subscribing to its event bus, translating
  tool events into the normalized v1 schema and POSTing them with the
  `BOARD_*` env forwarded; installed idempotently into the host
  project per the contract. Per the README's own advice, coverage
  starts coarse (`session`/`end` + generic `command`) and refines.
- `core/adapters/README.md` — update the contract: the third intent,
  the neutral allowlist env var, and opencode moving from "worked
  example" prose to a shipped reference implementation.

### Portability (the adapter contract must not become Claude-shaped)

Two things in this card touch the cross-vendor contract in
`core/adapters/README.md` and must land there, not only in `claude/`:

- **The intent vocabulary.** `AGENT_MODE` today carries `work | review`;
  this card gives act-pr a wider stance (push), so the contract gains
  that third intent (`AGENT_MODE=act-pr` or equivalent). Core signals
  intent; every adapter maps it to its own permission mechanism.
- **The allowlist data.** The project's runnable commands are expressed
  as plain command prefixes in `.env` (e.g. `python3 -m unittest`,
  `git commit`), never in any vendor's rule syntax. Each adapter renders
  them natively: Claude Code → `Bash(git commit:*)` allow-rules in the
  generated settings JSON; opencode → `"permission": {"bash":
  {"git commit *": "allow", "*": "deny"}}` in a generated config for
  `opencode run` (its wildcard rules, last-match-wins, and per-agent
  overrides map one-to-one). Both languages are prefix-pattern based, so
  neutral prefixes translate cleanly to either.

## Acceptance

- [ ] A relaunched work agent on a real task runs tests, commits, and the
      card reaches review/ with a non-empty branch and a real PR diff.
- [ ] A review agent can post its verdict to GitHub headlessly.
- [ ] An act-pr agent can push its follow-up commits.
- [ ] An agent that exits with zero commits is narrated as such in the
      ticker, not silently advanced.
- [ ] The allowlist default ships documented in `core/.env.example` and
      is overridable per project.
- [ ] `manager/core/adapters/opencode/` exists with `run` and `wire`
      honouring the contract; with `BOARD_AGENT_ADAPTER=opencode` and
      opencode installed, a work job on a scratch task runs headlessly,
      commits on its branch, and its events appear on the board in the
      normalized schema. (If opencode is not installed in the build
      environment, the adapter ships with its permission-config
      generation unit-tested and the live run is a documented reviewer
      step — mirroring how the claude adapter is stubbed in tests.)
- [ ] Review mode under the opencode adapter cannot edit files (config
      generation proves `"edit": "deny"` and the bash default-deny).
- [ ] `core/adapters/README.md` documents the third intent and the
      neutral allowlist variable, and points to both adapters as
      reference implementations.

## Open questions

- None.

## Notes

Discovered on the board's first self-hosted work launch (card 02).
Cleo's log, first line: "this session's permission mode denied every
attempt to execute `python3` … and every git write … so the work sits
uncommitted in the worktree." The agent handled the constraint as well
as possible — desk-checked every acceptance scenario and said clearly
what a reviewer must run — but the constraint itself is the bug.


---

## Work report — 2026-07-29 19:41 (Hazel)

ead reviews and line comments, so I granted them beyond the task's shorthand, following its own "grant what the prompt demands" principle); **review** = default mode, edit tools disallowed, + read-only git, `gh pr view/diff`, and exactly `gh pr review`/`gh pr comment`. Never bypassPermissions.

**Core** (`config.py`, `agents.py`): new `BOARD_AGENT_COMMANDS` setting (neutral comma-separated command prefixes, documented in `.env.example`, overridable in `local/.env`) passed to adapters as `AGENT_COMMANDS`; `start_pr_fix` now launches with the new third intent `act-pr`. **Empty-branch guard**: `_reap_agent` now refuses to advance a card whose branch got no commits — it stays in in-progress and the ticker says loudly that the agent committed nothing. Zero-commit detection is factored into `_no_new_commits`, shared with `_discard_untouched_worktree`.

**Opencode adapter** (`manager/core/adapters/opencode/`, new): `permission_config.py` renders the same three stances as opencode last-match-wins rules (`"*": "deny"` first, prefix allows after; `"edit": "deny"` in review); `run` hands the generated config to `opencode run` via `OPENCODE_CONFIG` (nothing written into the worktree), exit code passes through; `wire` idempotently installs `.opencode/plugin/bench-board.js`, a shim re-exporting `plugin.js`, which translates the event bus into the normalized v1 schema (coarse coverage: session/idle/end + one event per tool call).

**Docs**: `core/adapters/README.md` now specifies the three intents, the neutral `AGENT_COMMANDS` contract with both vendors' renderings, and both adapters as reference implementations; CLAUDE.md and `.env.example` updated to match.

**Tests** (`tests/`, new): claude allowlists per mode; both `run` scripts exercised end-to-end against stub binaries (asserting the flags and generated JSON a real launch would pass, including exit-code passthrough); opencode review mode proven `edit: deny` + bash default-deny; `wire` install/idempotence/dry-run; `_no_new_commits` against a scratch git repo.

### What a reviewer must run (in this worktree)

1. `python3 -m unittest discover -s tests` — should pass; fix anything it flags.
2. `chmod +x manager/core/adapters/opencode/run manager/core/adapters/opencode/wire` — chmod was denied; `agents.py` execs `run` directly, so the bit must be committed (`git add --chmod=+x` also works).
3. `git add -A && git commit` — nothing is committed; suggested message: `Launch headless agents with permissions that allow their own contract (task 05)`.
4. Live acceptance (needs the fix merged and the board restarted): relaunch a work agent on a real task and confirm it tests/commits; a review agent posts a verdict; opencode live run is the documented reviewer step if opencode is installed.

**Look first at**: the act-pr stance additions (`gh api` breadth), the guard branch in `_reap_agent` (report is now appended before the card moves), and the opencode plugin's event-type names against your installed opencode version.
