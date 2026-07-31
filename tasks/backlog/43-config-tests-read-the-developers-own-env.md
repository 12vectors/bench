# 43 — The suite reads the developer's own .env, so it passes or fails by whose machine it is on

**Status:** Backlog
**Priority:** Medium — two tests fail on any team-mode checkout and pass in
CI, which is the wrong way round for a suite people trust before pushing
**Type:** Bug

`tests/test_boards_sync.py` asserts what bench does when nothing is
configured, by deleting the settings from the process environment. But
"nothing configured" in this project means "whatever `manager/local/.env`
says", so on a checkout that has turned team mode on — the setting the
tests are about — the assertion reads that file and fails. CI has no
`.env`, so it never sees it.

## Context

- `manager/core/config.py:53-69` — `_load_env()` reads `local/.env` into
  a dict and then does `values.update(os.environ)`. Process environment
  beats the file, and the file beats the defaults. That precedence is
  right and documented; what follows from it is not obvious.
- `tests/test_boards_sync.py:481-496` — `TheGateImpliesCommitMoves.reload()`
  **pops** `BOARD_SYNC` and `BOARD_COMMIT_MOVES` from `os.environ` and
  reloads `config`, to stand for a machine that has set neither. Popping
  removes the only layer that was overriding `local/.env`, so the reload
  picks the file up instead of the defaults.
- The two that fail this way: `test_both_are_off_by_default` and
  `test_commit_moves_alone_stays_alone`. On this repo's own checkout
  (`BOARD_SYNC=1`, `BOARD_COMMIT_MOVES=1` in `manager/local/.env`) both
  fail; delete the file and both pass.
- Not confined to those two: any test that reloads `config` inherits
  whatever the developer's `.env` happens to say. It is invisible today
  only because nothing else asserts a default.
- The failure is the *good* direction of a bad property — a suite whose
  result depends on gitignored local state can as easily hide a real
  regression as invent a fake one.

**Affected areas:** `manager/core/config.py` and the config-reloading
tests, `tests/test_boards_sync.py` first.

## What to build

- **Give `config` one documented way to be pointed at a different env
  file**, and have the tests use it — an override read from the process
  environment (`BOARD_ENV_FILE`, say) that `_load_env()` honours instead
  of `LOCAL / ".env"`. It is the smallest change that makes every
  present and future config test hermetic rather than fixing two
  assertions.
- **Point the reloading tests at an empty file**, so "nothing
  configured" means exactly that. A test that wants a setting sets it in
  the environment as it does now.
- **Fail loudly if a test forgets.** A shared helper — or
  `tests/__init__.py`, which every run imports — that sets the override
  before `config` is first imported, so no individual test has to
  remember. Note that several test modules import `config` at module
  scope (`test_boards_sync.py:27`), so wherever this lands it has to
  happen first.
- **Say it where it will be read.** One line in the `_load_env()`
  docstring: the process environment is the only layer above the file,
  so removing a variable does not reveal the default, it reveals the
  file.

**Out of scope** — tempting neighbours left alone:

- The precedence itself. Environment over file over default is right,
  and every adapter and hook depends on it.
- Making `.env` reload at runtime, or watching it for changes.
- The two assertions' content. They are correct about what bench should
  do; they are just being answered by the wrong source.

## Acceptance

- [ ] Given `manager/local/.env` with `BOARD_SYNC=1`, when the full suite
      runs, then it passes — the state this checkout is in today, where
      it does not.
- [ ] Given no `manager/local/.env` at all, the suite still passes,
      exactly as it does in CI now.
- [ ] Given a `.env` that sets `BOARD_PORT`, `BOARD_AGENT_MODEL` or any
      other key, no test result changes.
- [ ] `test_both_are_off_by_default` fails if the defaults in
      `config.py` are actually changed — the test still tests something.
- [ ] Edge case: a test that deliberately wants a setting on still gets
      it from the process environment, and the override does not shadow
      that.

## Notes

Worth checking while in there whether anything *else* reads
`manager/local/.env` during a test run — the adapters get it through
`config.child_env()`, and a test that launches one would inherit the same
surprise.

**Risks** — an override for the env file's path is a setting that changes
where settings come from. Keep it environment-only and undocumented in
`.env.example`: a key inside `local/.env` that redirects `local/.env`
would be a genuinely confusing thing to leave lying around.
