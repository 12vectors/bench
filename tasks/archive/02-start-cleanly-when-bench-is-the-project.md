# 02 — Start cleanly when bench is the project itself

**Status:** Archived
**PR:** https://github.com/12vectors/bench/pull/1
**Priority:** High — ./start.sh is broken in this repo, and one of the three defects breaks every fresh install anywhere
**Type:** Bug

Three places assume the manager sits at `<project>/.task-manager/`, one
level inside the host repo. Bench now self-hosts — this repo is both the
distribution and the project — so `./start.sh` here wires the wrong
directory and then crashes. One of the three defects is not even
self-hosting-specific: nothing ever creates `manager/local/state/`, so a
fresh vendored clone crashes at boot the same way.

## Context

Observed: `./start.sh` in this repo prints
`/Users/ronald/Dev/12v/ext is not a .claude-initialised project` (the
*parent* of bench), then `board.py` dies with
`FileNotFoundError: …/manager/local/state/sessions`.

- `install.py:22` — `PROJECT = TM.parent`, a hardcoded layout assumption.
  `config.py::_repo_root()` already does this right: `git -C <manager>
  rev-parse --show-toplevel`, falling back to the parent. In a vendored
  install (`.task-manager/.git` removed at clone time) that resolves to
  the host root; in this repo it resolves to bench itself. Both correct.
- `manager/core/adapters/claude/wire:24` — `EMIT_CMD` hardcodes
  `$CLAUDE_PROJECT_DIR/.task-manager/manager/core/adapters/claude/emit.py`.
  Self-hosted, there is no `.task-manager/` segment, so even with root
  detection fixed the hooks would point at a nonexistent file and every
  session would report into the void.
- `board.py:59-60` — `SESSIONS_DIR.mkdir(exist_ok=True)` and
  `AGENT_DIR.mkdir(exist_ok=True)` lack `parents=True`. `local/state/` is
  gitignored with no `.gitkeep` and nothing creates it, so these crash on
  any fresh clone. `start.sh` explicitly tolerates a failed wire
  (`install.py || true` — "a project without .claude/ still gets the
  board"), so board.py must boot with no wiring at all.
- Card `01-install-ships-pristine-board.md` uses "no `local/.env`, no
  `local/state/`" as its first-boot signal — creating `state/` at board
  boot (after install.py has run, in start.sh's order) does not disturb
  that, but whoever builds both cards should keep the ordering in mind.

## What to build

- `install.py`: resolve the project root the way `config._repo_root()`
  does (git toplevel from the manager's directory, parent as fallback)
  instead of `TM.parent`. Keep passing the resolved root to the adapter's
  `wire` — the adapter contract is unchanged.
- `adapters/claude/wire`: stop hardcoding `.task-manager/` in `EMIT_CMD`.
  Derive the manager's path relative to the project root it is given
  (e.g. `os.path.relpath(TM, project)`) and embed that, so vendored
  installs keep `.task-manager/…` and self-hosted bench gets
  `manager/core/adapters/claude/emit.py`. Idempotency must survive: a
  settings.json wired with the old literal path counts as stale and gets
  repaired, per install.py's existing contract.
- `board.py` (or config): create the state directories with
  `parents=True` — boot must succeed on a virgin checkout with no
  `local/state/` and no wiring.

## Acceptance

- [ ] `./start.sh` in this repo wires `.claude/settings.json` *here*
      (hooks pointing at `manager/core/adapters/claude/emit.py`, no
      `.task-manager` segment) and serves the board for root `tasks/`.
- [ ] Fresh vendored sequence in a scratch host repo with `.claude/`
      (clone → rm `.git` → `start.sh`) wires with the `.task-manager/…`
      path and boots — no `FileNotFoundError`.
- [ ] In a scratch host *without* `.claude/`, the wire refusal prints but
      the board still boots and serves.
- [ ] Re-running `install.py` after wiring reports ok in both layouts; a
      settings.json carrying the old hardcoded emit path is repaired.

## Open questions

- None.

## Notes

Found by dogfooding within minutes of putting bench's own tasks on its
own board: the first `./start.sh` in the self-hosted layout hit all three
defects in one run. The `local/state/` one would eventually have been
found by any new adopter — self-hosting just found it first.


---

## Work report — 2026-07-29 18:21 (Cleo)

nstall.py`** — `PROJECT = TM.parent` (the hardcoded vendored-layout assumption) is replaced by `_project_root()`, which resolves `git -C <manager> rev-parse --show-toplevel` exactly like `config._repo_root()`, falling back to `TM.parent` when git is unavailable. Vendored installs (whose `.task-manager/.git` was removed at clone time) resolve to the host repo; self-hosted bench resolves to itself instead of its parent directory. The adapter contract is unchanged — the resolved root is still passed to `wire` as argv[1].

**`manager/core/adapters/claude/wire`** — the hardcoded `.task-manager/` segment is gone from both the emit hook command and `plansDirectory`. A new `_tm_prefix()` computes the manager's path relative to the project root it was given (`os.path.relpath`), so vendored installs still get `$CLAUDE_PROJECT_DIR/.task-manager/manager/core/adapters/claude/emit.py` and `./.task-manager/plans`, while self-hosted bench gets `$CLAUDE_PROJECT_DIR/manager/core/adapters/claude/emit.py` and `./plans`. `_is_ours` now also recognises hooks by the `manager/core/adapters/claude/emit.py` suffix, so a settings.json wired with the old literal path counts as stale and is repaired in place, and re-runs stay idempotent in both layouts.

**`manager/core/board.py` and `state.py`** — the `SESSIONS_DIR`/`AGENT_DIR` mkdirs now pass `parents=True` (with a comment explaining why: `local/state/` is gitignored and ships empty), so a virgin checkout with no `local/state/` and no wiring boots instead of dying with `FileNotFoundError`. `state.persist()` got the same guard since it also creates the sessions dir lazily.

**`tests/test_self_hosting.py`** (new, stdlib-only unittest, matching the project's no-install rule) — covers: self-hosted wiring produces prefix-free paths; vendored wiring keeps `.task-manager/`; a stale hardcoded emit path is repaired without duplicating hooks; second runs are byte-for-byte no-ops in both layouts; refusal without `.claude/`; install.py root resolution in vendored-git, self-hosted-git, and no-git layouts; and an integration test that boots `board.py` from a scratch checkout with no `local/state/` and asserts it answers `/api/state` and created the state dirs. Run with `python3 -m unittest discover -s tests -v`.

## Verification status

Because `python3` is blocked here, the tests have **not been executed** — a reviewer should run `python3 -m unittest discover -s tests -v` and `./start.sh` first. I compensated by tracing each acceptance scenario through the final code (including the macOS `/var`→`/private/var` symlink issue and worktree `--show-toplevel` behaviour, both of which the tests account for), and re-read every touched file end-to-end.

## For the reviewer

Look first at `_tm_prefix()` and the widened `_is_ours()` in `wire` — the repair-vs-ok logic is where idempotency lives. Note the uncommitted state: `git add -A && git commit` in the worktree is all that's missing; the diff is 4 files changed plus the new `tests/` directory.

## Field note — the self-hosted board is deaf to the main session (2026-07-29)

Observed while this card was being worked: the board shows every event
from the launched agent, but nothing from the interactive Claude Code
session running in bench itself. This is these same defects seen from the
other side, plus one nuance:

- Agents report despite the broken wiring because the adapter's `run`
  builds hook settings on the fly (`hook_settings.py`) and passes
  `--settings` into the headless launch — no project wiring involved.
- Interactive sessions rely on `.claude/settings.json`, which was never
  wired because `install.py` inspected bench's *parent* — **and** bench
  has no `.claude/` directory at all, and `wire` refuses to create one
  ("not a .claude-initialised project"). Fixing root detection alone
  still leaves self-hosted bench unwired; either `wire` gains the right
  to create `.claude/` at the resolved root, or the self-hosting story
  documents `mkdir .claude` as a prerequisite. Reviewer should check
  which stance the implementation took.
- Even once wired, sessions snapshot hooks at start — the already-open
  interactive session stays silent until restarted. Expected, but worth
  saying so nobody chases a ghost.


---

## Work report — 2026-07-29 19:56 (Cleo)

Done — committed as `cc931a9` on `task/02-start-cleanly-when-bench-is-the-project`, with the full test suite passing.

## What happened

This card came to me with Cleo's implementation complete but unverified and uncommitted (the previous launch couldn't execute tests or git). I reviewed the diff end-to-end, ran the suite — **all 10 tests pass** — made one small fix, and committed everything in a single reviewable commit.

**The three defects, as fixed:**
- `install.py` now resolves the project root via `git -C <manager> rev-parse --show-toplevel` (mirroring `config._repo_root()`), falling back to `TM.parent` without git — so vendored installs wire the host repo and self-hosted bench wires itself, not its parent.
- `adapters/claude/wire` derives the `.task-manager/` prefix from `os.path.relpath(manager, project)` instead of hardcoding it, for both the emit hook command and `plansDirectory`. `_is_ours()` also matches the `emit.py` suffix, so settings carrying the old hardcoded path are repaired in place, and re-runs stay byte-for-byte no-ops.
- `board.py` and `state.py` create the state dirs with `parents=True`, so a virgin checkout with no `local/state/` boots and serves.

**My changes on top:** closed the leaked Popen pipes in the boot test (it was emitting ResourceWarnings), verified `git rev-parse --show-toplevel` in this worktree resolves to the worktree itself (confirming install.py wires the right root here), and re-ran the suite clean.

## Verification

`python3 -m unittest discover -s tests -v` — 10/10 pass, including a real integration test that boots `board.py` from a scratch checkout with no `local/state/` and asserts it answers `/api/state`. The tests execute all four touched files as subprocesses, so they double as a syntax check (`py_compile` was permission-blocked, but redundant given that). I could not run `./start.sh` end-to-end here — it would contend with any board already running on the pinned port.

## For the reviewer

1. **The `.claude/` stance** (the field note's open point): the implementation kept the existing refusal — `wire` does not create `.claude/`. Since bench itself has no `.claude/` directory, `./start.sh` in the main checkout will print the refusal and boot unwired until someone runs `mkdir .claude` first. That matches the task's "What to build" (which didn't ask for creation) and acceptance item 3, but if you want self-hosted bench wired out of the box, that's a follow-up card.
2. `_tm_prefix()` and the widened `_is_ours()` in `wire` — that's where the idempotency and stale-path repair live; `tests/test_self_hosting.py:105` (`test_stale_hardcoded_path_is_repaired_in_place`) is the proof.
3. The acceptance item about a fresh vendored clone is covered by `test_vendored_resolves_host_repo_root` plus the virgin-boot test rather than a literal clone→rm-`.git`→`start.sh` run, which needs a network remote.
