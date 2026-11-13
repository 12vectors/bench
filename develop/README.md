# develop/ — for people developing bench itself

Nothing here is installation material. No release ships this directory,
and no install, reinstall or update reads it, copies it or points an
agent at it. A bench checkout used to install bench into another project
carries it as inert files.

## Why it exists

`manager/local/` belongs to the project an install manages: its workflow
notes (`AGENTS.md`, with `CLAUDE.md` as the pointer), its `checks`, its
driver, commands, prompt overrides, `.env`, profile document and state.
Up to task 78, bench's repository tracked its *own* `manager/local/AGENTS.md`
and `checks` there, so a source checkout staged for another project told
that project's agents that the definition of done was `python3 -m
unittest`. Those two files now live here:

| File | What it is |
| --- | --- |
| `operator-local/AGENTS.md` | bench's definition of done, for an operator whose application is a bench checkout |
| `operator-local/checks` | the Focus view's CHECKS matcher for bench's suite |

Bench's own repository tracks nothing in `manager/local/` but the
`.gitkeep` placeholders. The neutral starters a fresh install gets are
tool-owned seeds in `manager/core/seed/local/`, which `install.py` copies
one file at a time and only where a file is missing.

## Configuring an operator that develops bench

The operator is the install you run the board from; the candidate is the
bench checkout named as its application (see "An operator install, and
the bench under test" in the root `AGENTS.md`). To give the operator's
agents bench's own policy, copy it in by hand. Nothing does it for you,
and nothing undoes it:

```bash
# in the operator install
cp -n <bench checkout>/develop/operator-local/AGENTS.md manager/local/AGENTS.md
cp -n <bench checkout>/develop/operator-local/checks    manager/local/checks
```

`cp -n` refuses to replace a file that is already there. If the operator
already has `manager/local/AGENTS.md` (the seeded starter, or notes of
its own), merge the definition-of-done paragraph into it instead.

The test runner a headless run may launch is a separate setting. The
shipped default for `BOARD_AGENT_COMMANDS` is now empty, so an operator
that relied on the old `python3 -m unittest` default, and has no profile
document granting bench's checks, sets it in `manager/local/.env`:

```bash
BOARD_AGENT_COMMANDS=python3 -m unittest
```

None of this reaches any other install: these files stay in that
operator's `manager/local/`, which no update touches.

## Updating a source checkout made before task 78

A checkout made before task 78 tracks `manager/local/AGENTS.md`,
`CLAUDE.md` and `checks`. The update that stops tracking them would
delete an untouched copy and refuse to pull over an edited one, so run
the preflight from the revision you are updating to before you pull:

```bash
git fetch origin
git show origin/main:protect-local.py > /tmp/protect-local.py
python3 /tmp/protect-local.py            # report; changes nothing
python3 /tmp/protect-local.py --apply    # copy aside what is yours
git pull --ff-only
python3 install.py                       # puts yours back, seeds the rest
```

What each file gets:

- **bench's version, untouched** — removed by the pull. `install.py`
  seeds the neutral starter for `AGENTS.md` and `CLAUDE.md`; `checks`
  falls back to `manager/core/checks`. Bench's unittest policy is not
  kept, which is the point of the change. An operator that develops bench
  copies it back from `operator-local/` as above.
- **your edit of bench's version** — copied to
  `manager/local/state/migrations/local-ownership/` and verified by hash,
  then put back to the committed version so the pull can remove it.
  `install.py` restores your version afterwards, byte for byte.
- **your own committed version** — copied the same way and untracked
  with `git rm --cached`; it stays on disk. Commit the untracking, merge
  the update, then run `install.py`.
- **anything ambiguous** (a link, a conflict, staged and unstaged
  versions at once, a file edited again after it was protected, an
  untracked file where the update adds one): nothing changes, every case
  is named, and the exit status is 2. Settle it and run the preflight
  again.

Run it as often as you like: a protected file is reported as protected,
its copy is not written twice, and after the update there is nothing left
to judge. Restoring never overwrites. A different file found in place is
kept, the protected copy is kept, and both are named. `python3
protect-local.py --restore` does the restoring step on its own.

A vendored source install (a clone with `.git` removed) and a release
install have nothing to migrate: no update there touches `manager/local/`.
