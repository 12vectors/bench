# 44 — Archiving a card reaches git, and so does every other write the board makes to a task file

**Status:** Backlog
**Priority:** High — an archive silently stalls team-mode sync, and the
board says nothing about why
**Type:** Bug

Moving a card between stages commits itself. Archiving one does not — it
renames the file on disk and stops there. In team mode that leaves an
uncommitted deletion of a tracked file, which is precisely the condition
`sync` refuses to run over, so a single drag onto the archive tray stops
every board commit from publishing until a human notices and commits by
hand. Agent reports appended to a task file have the same gap.

## Context

- `manager/core/taskfiles.py:108` — `archive_task()` rewrites the
  `**Status:**` line to `Archived`, `shutil.move`s the file into
  `tasks/archive/`, and returns. `unarchive_task()` (`:130`) is the same
  in reverse. Neither calls `_commit_move()`, `commit_edit()` or
  `state.expect_move()`.
- `manager/core/taskfiles.py:261` — `move_task()`, for comparison, sets
  the assignee, calls `state.expect_move()` and commits the whole change
  in one `board: <n> → <stage> (<name>)` commit when `COMMIT_MOVES` is
  on. Archiving is the same kind of user action against the same
  directory tree, and gets none of it.
- `manager/core/agents.py:35` — `_file_report()` appends a run's closing
  report to the task file and never commits either. `commit_edit()`
  exists for exactly this and is called from only two places
  (`github.py:73` for the `**PR:**` line, `taskfiles.py:199` for a
  claim).

The live evidence, in this repo's own working tree while the card was
written:

```
 D tasks/done/11-failed-agent-runs-must-be-visible.md
 D tasks/done/15-distribute-bench-as-packaged-releases.md
 M tasks/to-do/36-the-reference-section.md
?? tasks/archive/11-failed-agent-runs-must-be-visible.md
?? tasks/archive/15-distribute-bench-as-packaged-releases.md
```

Two cards archived through the board: the source deleted and
uncommitted, the archived copy untracked, and a third card modified by
an appended report. `sync._clean()` (`sync.py:113`) runs
`git status --porcelain --untracked-files=no`, so those two deletions
alone make it false — this board has not published a commit since,
and the reason is a drag nobody would connect to sync.

Three consequences, in the order they bite:

1. **Sync stalls for everyone on this board.** Not just the archive: every
   later card move queues behind it.
2. **The archive never reaches the team.** The archived file is untracked,
   so other boards keep showing a card its owner archived.
3. **A card can be lost to a clean checkout.** The archived copy exists
   only in one working tree. `git checkout .` or a fresh clone loses it,
   which is a poor outcome for a feature whose promise is "out of every
   column, never deleted".

**Affected areas:** `manager/core/taskfiles.py` (archive and unarchive),
`manager/core/agents.py` (`_file_report`), and `manager/core/httpd.py`
where the archive routes are served.

## What to build

- **Archive and unarchive commit themselves**, exactly as a move does,
  under the same `COMMIT_MOVES` gate — solo boards keep committing
  `tasks/` by hand and nothing changes for them. One commit, staged by
  pathspec, naming both paths so the rename is recorded rather than a
  delete and an add.
- **A message that reads like the others.** `board: <n> → archived
  (<name>)` and `board: <n> → <stage> (<name>)` on the way back. The
  `board: ` prefix is not cosmetic: `sync`'s piggyback guard
  (`sync.py:157`) refuses to publish any local-ahead commit without it,
  so a differently-worded message would stall sync just as thoroughly as
  committing nothing.
- **Push it like a move.** `_commit()` already calls
  `state.task_committed()`, which is what event-driven push hangs off, so
  routing through the same helper gets this for free — worth asserting
  rather than assuming.
- **Appended reports commit too.** `_file_report()` should use
  `commit_edit()` the way the `**PR:**` line does. A work report is the
  permanent record the project keeps on purpose; leaving it uncommitted
  both loses it and stalls sync.
- **Decide about `expect_move`.** `archive_task` does not call
  `state.expect_move()`, so the watcher sees a card vanish from a stage
  directory with no expectation registered. Check what it narrates today
  and make the archive attribute itself the way a move does — `you`, not
  `disk`.

**Out of scope** — tempting neighbours left alone:

- The ⌘Z undo behaviour itself, which works; this is about what it
  leaves behind in git.
- Any change to what may be archived (backlog, to-do and done only) or
  to the tray.
- Making sync tolerate a dirty tree. It refuses on purpose — the fix is
  for the board to stop making the tree dirty behind the user's back.

## Acceptance

- [ ] Given `BOARD_COMMIT_MOVES` on, when a card is dragged to the
      archive tray, then `git status` is clean afterwards and one
      `board: `-prefixed commit records the move into `tasks/archive/`.
- [ ] Given `BOARD_SYNC` on, when a card is archived, then the commit
      pushes on its own and a second board shows the card gone on its
      next beat.
- [ ] ⌘Z produces its own commit restoring the card to the stage it came
      from, and the tree is clean after that too.
- [ ] Given a work agent that finishes, when its report is appended to
      the task file, then that write is committed too — no run leaves a
      modified task file behind.
- [ ] Given `BOARD_COMMIT_MOVES` off, archiving commits nothing and
      behaves exactly as it does today.
- [ ] The archive is attributed on the ticker to the person who did it,
      not to `disk`.
- [ ] Edge case: archiving a card that was never committed (a brand-new
      backlog file) commits it as an addition in `tasks/archive/` rather
      than failing on a pathspec that matches nothing in HEAD.

## Notes

The pattern worth taking from this: `tasks/` has exactly one module
allowed to touch it, and inside that module a *move* knows it has to
commit while an *archive* and an *append* do not. Whatever fixes this
should make committing the property of writing to a task file, not
something each caller remembers — otherwise the next writer added to
`taskfiles.py` will forget in the same way.

**Risks** — the two archived cards and the appended report sitting
uncommitted in this checkout right now need committing by hand before
sync resumes; that is separate from this card and should not wait for it.
