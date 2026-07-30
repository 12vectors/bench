# 18 — Cards are claimed on move: assignee written, board commits the change

**Status:** Backlog
**Priority:** High — the ownership primitive every other multi-user piece builds on
**Type:** Feature

Cards have no owner: the face shows "nobody yet" and nothing records
who picked work up. For one person that's cosmetic; for a team it's
the missing primitive. Moving a card out of backlog claims it — the
board writes the assignee into the file and commits the move — so
ownership travels with the card to every clone, and attribution stops
being an in-memory courtesy.

## Context

- Task files carry Status/Priority/Type but no assignee; the card
  face's "nobody yet" slot is the UI waiting for this value.
- Board moves today rename the file and rewrite Status
  (`taskfiles.py`) but git never hears about it — commits of tasks/
  happen by hand, so the shared history lags the board by hours.
- Identity: `git config user.name` — already present on every machine
  that can commit, already what blame/history show, no new concept.
- The 2s disk watcher narrates hand-moves; nothing in this card
  changes that — it gains attribution in task 19 when remote moves
  arrive via sync.

**Affected areas:** `taskfiles.py` (the assignee line, the commit),
`board.html` (render assignee on the face), AGENTS.md (the convention).

## What to build

- Moving a card backlog → to-do or to-do → in-progress through the
  board writes `**Assignee:** <git user.name>` into the header (first
  claim only — an existing assignee is preserved, not overwritten).
  Walking a card back to backlog clears it.
- Board-made task changes commit themselves: the move + claim in ONE
  commit touching only that task file, message prefixed `board: ` with
  the actor and transition (`board: 18 → in-progress (ronald)`).
  Commit only — pushing is task 19's job. Gate the auto-commit behind
  `BOARD_COMMIT_MOVES` (default off) so single-player behaviour is
  unchanged until opted in.
- The card face replaces "nobody yet" with the assignee; done/archive
  keep it as history.
- AGENTS.md documents the convention: claiming is moving; the assignee
  launches agents on the card; hand-moves should update the line too.

**Out of scope** — tempting neighbours left alone:

- Pushing, pulling, or any cross-machine behaviour (task 19).
- Enforcing assignee-only launches (task 20).
- Multiple assignees, @-mentions, or any identity beyond git's.

## Acceptance

- [ ] Given an unclaimed backlog card, when it is dragged to to-do on
      a board with `BOARD_COMMIT_MOVES=1`, then the file gains
      `**Assignee:**` with the mover's git name and exactly one
      `board: `-prefixed commit exists touching exactly that file.
- [ ] Given an already-assigned card, when someone else moves it
      forward, then the assignee is unchanged (first claim sticks).
- [ ] Given a claimed card walked back to backlog, then the assignee
      line is removed.
- [ ] With the gate off (default), moves behave byte-identically to
      today: no commit, no assignee write unless configured.
- [ ] Edge case — a dirty index: the board's commit stages only the
      task file's paths; a developer's unrelated staged changes are
      neither committed nor unstaged.

## Open questions

- None.

## Notes

First card of the multi-user arc (18 claim → 19 sync → 20 etiquette),
from the 2026-07-30 design discussion: origin/main becomes the truth
and every checkout a replica, with git as the lock server. The claim
must be atomic with the move — same commit — because it is the
optimistic lock task 19's push races resolve.

**Risks**

- Hand-moves (plain `mv`) bypass the claim; the watcher still narrates
  them but no assignee is written. Acceptable — AGENTS.md says to
  update the line — but the gap should be stated, not hidden.
- `user.name` collisions ("ronald" on two machines) merge identities;
  fine for teams that also share a git history, worth one doc line.
