# 01 — Ship a pristine board on install, not bench's own cards

**Status:** Backlog
**Priority:** High — every install made after we start dogfooding inherits our cards
**Type:** Bug

Bench now tracks its own development on its own board: task cards live in
this repo's root `tasks/`, which `config.py` already resolves natively.
But installing bench into a host project is `git clone <this repo>
.task-manager` (README, "Install into a repo") — the clone carries
whatever sits in our stage directories, so a new user's first board opens
pre-loaded with bench's backlog, done-pile and plans. Install should
deliver a pristine board while this repo keeps its full task history.

## Context

- `README.md` install: `git clone <this repo> .task-manager && rm -rf
  .task-manager/.git`, then `./.task-manager/start.sh` (which runs
  `install.py`). Nothing in that path touches `tasks/`, `plans/` or
  `reference/`, so distribution-side content ships verbatim.
- `install.py` is idempotent and runs on **every** start — it must never
  clean stage directories in normal operation, or it would eat the host
  project's own cards. Any cleaning has to be provably first-boot-only.
- `update.sh` is already careful never to touch `tasks/` — this task must
  not change that.
- This card is itself the proof of the problem: it would appear on every
  fresh install until this is fixed.

## What to build

- First-boot detection in `install.py`: before `manager/local/` has been
  populated (no `local/.env`, no `local/state/`), the install has never
  been wired, so any files in the stage directories, `tasks/archive/`,
  `plans/` and `reference/` can only be the distribution's own. In that
  state — and only then — remove them, keeping `task-template.md` and the
  `.gitkeep` files, and print each removed path so nothing vanishes
  silently. Respect `--dry-run`.
- Once wiring has happened even once, the guard is permanently false and
  `install.py` never touches those directories again — idempotency for
  hosts is preserved.
- README note under "Install into a repo": one sentence saying the first
  `start.sh` clears the distribution's own cards, so a fresh install
  starts empty.

## Acceptance

- [ ] Fresh sequence in a scratch host repo (clone → rm `.git` →
      `start.sh`) yields empty stage dirs, empty `plans/` and
      `reference/`, with `task-template.md` and `.gitkeep` files intact,
      and the removals printed.
- [ ] Running `install.py` a second time in that host removes nothing and
      reports "ok" — cards the host has since created survive.
- [ ] In this repo (bench itself, already wired), `install.py` and
      `start.sh` leave `tasks/`, `plans/` and `reference/` untouched.
- [ ] `install.py --dry-run` on a fresh clone lists what would be removed
      without removing it.

## Open questions

- None.

## Notes

Origin: first card written when bench started tracking its own tasks in
root `tasks/` — the decision that made this bug real. The alternative
(self-installing a `.task-manager/` inside bench) was rejected: it doubles
the manager code, runs the board from a stale vendored copy, and would
nest an installation inside every future clone.
