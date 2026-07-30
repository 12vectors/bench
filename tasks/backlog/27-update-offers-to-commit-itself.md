# 27 — update.sh offers to commit the update it just made

**Status:** Backlog
**Priority:** Medium — every update leaves doctrine-mandated homework in the host's tree; the first 0.2 field report asked for exactly this
**Type:** Feature

The doctrine says commit `.task-manager/` into the host repo, so every
update strands a large vendored diff (0.1→0.2: 18 files, +1049/−275)
in the working tree for someone to stage by hand — on a branch teams
try to keep clean. update.sh knows exactly which paths it replaced, so
it can stage exactly those and commit them cleanly — offered, never
imposed.

## Context

- Field report (first 0.2 update, cicero-pas install): update clean,
  promise held (only core-owned files touched), but the tree was left
  holding the vendor diff *alongside the host's own uncommitted card
  move* — and the human had to know to separate them.
- The separation is mechanical: the artifact's `release-manifest`
  names every path the update may write (`tree manager/core` + the
  `copy` list). Stage precisely those; the host's own dirt (card
  moves, local edits) stays untouched.
- Interaction with task 19's piggyback guard: on a synced board, a
  non-`board:` commit on main stalls publishing with a stray-commit
  note. A manifest-scoped update commit is mechanical and reproducible
  from its release tag — it should count as publishable.

**Affected areas:** `update.sh`, `sync.py` (`_stray` learns the
`bench: ` prefix), `core/.env.example`, README's update section.

## What to build

- After a successful update, update.sh stages the manifest's paths
  and either commits — message
  `bench: core <old> → <new> (<tag>)` — or prints the exact command
  it would run. Committing requires opt-in: `--commit` flag or
  `BENCH_UPDATE_COMMIT=1` in local/.env; default prints.
- Nothing outside the manifest's paths is ever staged — proven
  against a tree that also carries an unrelated task move and a
  dirty file elsewhere in the host repo.
- `sync.py`'s publish guard accepts `bench: ` commits as pushable
  alongside `board: ` ones; everything else still stalls the push.
- README/update output: one line telling users with sync on that the
  update commit will publish on the next beat.

**Out of scope** — tempting neighbours left alone:

- Auto-pushing the commit from update.sh itself (sync's job where
  enabled; the human's otherwise).
- Committing on the bench repo's own self-hosted layout — dev clones
  update via git, and update.sh's no-release path already says so.

## Acceptance

- [ ] Given a host tree with an uncommitted card move plus the fresh
      update, when `update.sh --commit` runs, then one `bench: `
      commit contains exactly the manifest paths and the card move
      remains uncommitted.
- [ ] Without the opt-in, nothing is committed and the printed
      command, run verbatim, produces the identical commit.
- [ ] With `BOARD_SYNC=1`, the update commit publishes on the next
      beat; a genuinely stray commit still stalls with the named
      note.
- [ ] Edge case — update applied but commit refused (e.g. the host's
      git identity unset): the update itself stays intact and the
      failure names what to fix; no partial staging left behind.

## Open questions

- None.

## Notes

Requested from the field (2026-07-30) after the first 0.2 update:
"main is typically a clean branch — should we offer to commit as part
of an install update?" Yes to offering, no to imposing: the manifest
is what turns the offer from "commit -A and hope" into surgery.
