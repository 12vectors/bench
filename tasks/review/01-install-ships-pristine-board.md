# 01 — Ship a pristine board on install, not bench's own cards

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/5
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


---

## Work report — 2026-07-30 07:10 (Cleo)

The work is complete — committed on this branch and fully tested. Closing report:

---

The fix is implemented, committed (`44537ae`), and verified: the full test suite (54 tests, including 5 new ones) passes, and a fresh vendored install now boots with an empty board while bench's own repo keeps its task history untouched.

**What changed**

- `install.py` gained first-boot detection that runs before delegating to the adapter's `wire`. It fires only when the install is vendored (project root differs from the manager's root) **and** `manager/local/` has never been populated (no `.env`, no `state/`). In that state it removes everything in the five stage directories, `tasks/archive/`, `plans/` and `reference/`, keeps `task-template.md` and every `.gitkeep`, and prints each removal (`removed <path>`, or `would remove <path>` under `--dry-run`). After a real (non-dry-run) clean it creates `manager/local/state/`, so the guard is permanently false from then on — even if the adapter wire fails, which matters because `start.sh` serves the board to hosts without `.claude/` and their cards must never be eaten on a later run.
- `README.md` notes under "Install into a repo" that the first `start.sh` clears the distribution's own cards.
- `tests/test_install_first_boot.py` exercises `install.py` end-to-end as a subprocess against scratch host layouts: first run cleans and prints; second run removes nothing and a host-created card survives; `--dry-run` lists without removing and without stamping; a pre-existing `local/.env` disarms the guard; a self-hosted repo is never cleaned.

**One decision to know about, beyond the task's letter:** the task defined first boot purely by the absence of `local/.env` and `local/state/`, but that alone would also match a fresh development clone of bench itself and delete its git-tracked cards. I added a second condition — cleaning only happens when the install is vendored (project root ≠ manager root, the same resolution `config._repo_root` uses) — so self-hosted checkouts are structurally exempt. This is what makes acceptance point three hold even for an unwired bench clone.

**For the reviewer**

- Start with `first_boot_leftovers` and `first_boot_clean` in `install.py` — the guard logic and the stamp ordering are the whole risk surface.
- Run `python3 -m unittest discover -s tests` to reproduce the green run.
- One acceptance step I could not run literally: the sandbox denies executing `install.py` directly in this checkout, so "bench itself is untouched" is proven by `test_self_hosted_repo_is_never_cleaned` (which builds a git-initialised self-hosted layout) rather than by a live run here. The scratch-host tests cover the clone → first-run → second-run sequence the other acceptance points describe.
