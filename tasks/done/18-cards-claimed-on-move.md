# 18 — Cards are claimed on move: assignee written, board commits the change

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/15
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


---

## Work report — 2026-07-30 09:06 (Otto)

onfig user.name` into the header (first claim only — an existing assignee is never overwritten), and clears the line when a card is walked back to `backlog/`. The same move then commits itself through `_commit_move`: one commit containing the move and the claim, staged by pathspec (`git add -A -- <old> <new>`, then a pathspec `git commit --no-verify`), messaged `board: 18 → to-do (ronald)`. A commit that fails narrates in the ticker rather than raising — the card has already moved, and disk is the source of truth.
- **`manager/core/config.py`** — `BOARD_COMMIT_MOVES` (default off) plus a small `flag()` helper for boolean settings. The gate governs both the assignee write and the commit, so with it off a move is byte-identical to before.
- **`manager/core/board.html`** — the card face shows the assignee instead of `nobody yet` in every stage (on `done/` cards it reads as history); the who row is now escaped, since its content comes from a file.
- **`AGENTS.md` / `manager/core/.env.example`** — a "Claiming a card" section (claiming is moving, the assignee launches agents, hand-moves bypass the claim, git identities collide like git's), the `**Assignee:**` field in the task-file-format section, and the setting documented with its default.
- **`tests/test_claim_on_move.py`** — 18 new tests. Each runs `move_task` against a throwaway git repo, so the commit behaviour is checked against real git: claim written and exactly one commit touching exactly the two task paths, first claim sticks when a second mover advances the card, walk-back clears the line, gate-off produces the old bytes and no commit, a dirty index survives (staged change still staged, absent from the commit), and a failing commit still moves the card and says so.

## What a reviewer should look at first

1. **Unpushed `main` when the gate is on.** `github.complete_task` merges and pushes `main`, *then* calls `move_task`, so the resulting `board: … → done` commit sits unpushed on local `main`. The next card entering `review/` will hit the existing "main is N commits ahead" guard and refuse to open a PR until you push. Pushing is explicitly task 19's scope, so I left it and documented the consequence in AGENTS.md — worth confirming that's the intended seam.
2. **Which transitions claim.** The card names `backlog → to-do` and `to-do → in-progress`; I implemented it as "any forward move out of `backlog/` or `to-do/`", which also covers a two-column drag straight to `in-progress` or `review`. `taskfiles.claims()` is the whole rule and is tested directly.
3. **`--no-verify` on the board's commit.** A project pre-commit hook could otherwise block or rewrite a card move; I judged the board's bookkeeping commit should skip hooks. It's a deliberate choice, noted in the code and in AGENTS.md.

One gap to know about, stated rather than hidden: `archive_task` / `unarchive_task` neither claim nor commit — archived cards simply keep whatever assignee they had. Only `move_task` participates in team mode.


---

## PR update — 2026-07-30 09:16 (Otto)

ADDRESSED: Reworded the "gate off" docs in AGENTS.md and manager/core/.env.example to say the gate governs only whether a *move* writes/clears the assignee — an assignee already in a file is still read and shown either way.

- **Copilot, `manager/core/.env.example:66` — "gate off … no assignee" is misleading.** Addressed. The line read "no assignee," implying assignees can't exist or display when the gate is off. In fact `read_task()` (`manager/core/taskfiles.py:55`) parses the `**Assignee:**` line unconditionally and the card face always renders it; only `move_task`'s write/clear/commit is gated (`manager/core/taskfiles.py:241`). Reworded to: gate off means moves neither write nor clear the assignee and never commit, but an assignee added by hand is still read and shown.
- **Copilot, `AGENTS.md:432` — same "no assignee" implication in the "Claiming a card" section.** Addressed with the matching clarification: "The gate governs only whether a *move* writes the line — an **Assignee:** added to a file by hand is still read and shown on the card whether the gate is on or off."

Both are the same point raised against the two doc files; no code change was needed because the code already behaves as clarified. This is documentation-only, so no new tests; the full suite (`python3 -m unittest discover -s tests -v`, 171 tests) still passes, including `test_gate_off_leaves_a_claimed_card_claimed`, which already asserts an existing assignee survives a gate-off move. PR remains MERGEABLE.

- To know: no behavior changed and nothing else in the PR was touched. Nothing for the reader to run or decide beyond the normal merge call.
