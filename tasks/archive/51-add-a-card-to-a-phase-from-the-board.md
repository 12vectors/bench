# 51 — Add a card to a phase without opening the file

**Status:** Archived
**PR:** https://github.com/12vectors/bench/pull/43
**Assignee:** istos
**Priority:** Low — the convenience path; phase cards mostly arrive with
their lists already written
**Type:** Feature
**Depends on:** 48 — the list this appends to has to exist and be read

A `⟶ phase` action on a backlog or to-do card, offering the phase cards
currently in `to-do/` and appending the card to the one you pick. It is
the small path: most phases are written whole, members and dependencies
already in place, before anything reaches the board. This is for the card
you decide belongs after all.

## Context

- The phase card's `## Cards` section is the single place membership
  lives (48), so adding a card means appending one line to *that* file —
  not editing the card being added.
- The board already writes into task files where the file must stay the
  source of truth: the `**PR:**` line goes in through `commit_edit()`
  (`manager/core/taskfiles.py:242`), which commits under the same gate a
  move does and, in team mode, reaches the other boards.
- A choice between several options has a pattern too: the drag-to-`done/`
  sheet (`completeSheet`, `board.html:1362`) puts a short list of named
  outcomes in front of you rather than guessing.
- Card actions arm before they fire, and the status pill's slot holds at
  most two per state. On backlog and to-do cards that slot is close to
  free — `◔ still true?` is often the only one there.
- Task `../backlog/47-an-archive-button-on-the-card.md` puts a chip in
  the footer row for a similar reason; the two should not fight for the
  same corner.

**Affected areas:** `manager/core/board.html` (the action and its sheet),
`manager/core/httpd.py` (a route), `manager/core/taskfiles.py` (the
append).

## What to build

- **A `⟶ phase` action** on cards in `backlog/` and `to-do/` that are not
  already in a phase, and not themselves phase cards.
- **A sheet listing the phase cards in `to-do/`**, each with its number
  and title and how many cards it already holds. Picking one appends the
  card to the end of that phase's `## Cards` section and closes the
  sheet.
- **`to-do/` only.** A phase in `in-progress/` is running: its branch
  exists, its members are being worked in the order the list had when it
  started, and appending mid-flight is a different feature with different
  questions. Offer it and someone will find out the hard way.
- **The append goes through the board's own write path**, so it commits
  itself under `BOARD_COMMIT_MOVES` and syncs like every other board-made
  change to a task file. An addition that never leaves one working tree
  is not an addition the phase will run.
- **Written the way a person writes it** — `- 33 — <title>` — so the
  section stays something you would have typed. A machine-shaped line in
  a file people author by hand is how a format stops being pleasant.
- **Nothing else moves.** The card stays in its stage; joining a phase is
  not a commitment to start it, and the phase decides when it runs.
- **No phases in `to-do/` → the action is absent**, not present and
  empty. The board offers what it can do.

**Out of scope** — tempting neighbours left alone:

- Removing a card from a phase, and reordering the list. Both are file
  edits for now; both are better judged once phases have run a few
  times.
- Adding to a running phase.
- Creating a phase card from the board. Phase cards are written, and
  writing them is where their brief comes from.
- Multi-select — adding several cards in one gesture — which needs a
  selection model the board does not have.

## Acceptance

- [ ] Given a card in `backlog/` or `to-do/` and at least one phase card
      in `to-do/`, hovering offers `⟶ phase`; choosing a phase appends
      the card to that phase's `## Cards` and the member chip from 48
      appears on it.
- [ ] The appended line names the card's number and title, at the end of
      the section, and the rest of the phase card is untouched.
- [ ] With `BOARD_COMMIT_MOVES` on, the append is committed on its own,
      messaged like the board's other bookkeeping; with it off, nothing
      is committed and the file is simply edited.
- [ ] Phase cards in `in-progress/`, `review/` or `done/` are not
      offered.
- [ ] A card already in a phase does not offer the action.
- [ ] With no phase in `to-do/`, the action does not appear.
- [ ] Edge case: a phase card with no `## Cards` section yet gets one,
      rather than the line being appended to the end of the file.
- [ ] Edge case: two boards adding to the same phase produce two lines,
      not a lost one — the second append reads the file as it is on disk
      rather than as it was rendered.

## Notes

The reason this is Low and not Medium: the intended way a phase arrives
is fully formed — a card written with its members listed and each
member's `Depends on` filled in — because that is a thing you can read in
a diff and reason about before any of it runs. This action is for the
afterthought, and it should stay small enough not to become the way
phases are assembled.


---

## Work report — 2026-08-01 11:23 (Juno)

Done and committed on `task/51-add-a-card-to-a-phase-from-the-board` as `463b35b`; the full suite (831 tests) passes on exactly that tree.

**What changed**

A `⟶ phase` action on `backlog/` and `to-do/` cards that are not already in a phase and are not phase cards themselves. It opens a sheet naming the phase cards waiting in `to-do/` — each with its number-and-title and how many cards it already holds — and picking one appends `- <n> — <title>` to the end of that phase's `## Cards` section. The card itself is not touched and does not move; the member chip from task 48 appears on it because membership is derived from the phase card's list.

- `manager/core/taskfiles.py` — `add_to_phase()`, which validates and then writes through the existing `append_to_section()` door, so the append commits itself under `BOARD_COMMIT_MOVES` (`board: 40 gained 51 (<name>)`), publishes to sync, creates the `## Cards` section when the phase card has none, and re-reads the phase card off disk rather than off a render. Refusals: a phase outside `to-do/`, a card outside `backlog/`/`to-do/`, a card any phase already lists, a phase card (no nesting), a card with no number, a stale filename, a path where a filename belongs.
- `manager/core/httpd.py` — `POST /api/phase/add`, narrating a `phase`-kind board event and broadcasting so every open tab re-reads.
- `manager/core/board.html` — `joinablePhases()` (which is both the candidate list and whether the action exists at all), the action itself, `phaseSheet()` and `addToPhase()`.
- `AGENTS.md` — the action in the hover-actions list and a paragraph in the phase-membership section.
- `tests/test_add_to_phase.py` — 32 tests: the line's shape and position, the empty-section and missing-section cases, the two-boards-append case, every refusal, the commit gate on and off against a real throwaway git repo, and board.html source invariants plus a `node --check` parse.

**Worth a reviewer's eye first**

- `_member_entry()` in `manager/core/taskfiles.py:493` drops the number the title usually repeats (`# 51 — Add a card…` → `- 51 — Add a card…`), using the same cut `phaseLabel()` makes in the page. That is the one place a judgement was made about what the written line should read like.
- The already-in-a-phase guard (`_phase_holding`) scans every task file on every add. That is the same cost `collect()` pays per request, so it is in keeping, but it is a full-board read behind one click.
- The action does not arm before firing: it writes nothing on click, it only opens the sheet, and the sheet's named choice is the confirmation — the same shape as drag-to-`done/`. If the house rule is read as "every action arms", that is the line to argue with.

Nothing in the task's out-of-scope list was built: no removal, no reordering, no adding to a running phase, no phase creation, no multi-select.


---

## PR review — 2026-08-01 16:39 (Wren)

PR REVIEW: APPROVE

Work is committed on `task/51-add-a-card-to-a-phase-from-the-board` (`463b35b`) and, per the work report, the full suite (831 tests) passes on that tree; the feature is complete and I found nothing that must change. GitHub refused a formal approval because the PR author and this reviewing account are the same identity, so the verdict was posted as a PR comment instead — the `PR REVIEW: APPROVE` marker above is the record the board parses.

**What I checked**
- The full diff (`AGENTS.md`, `board.html`, `httpd.py`, `taskfiles.py`, and the new `tests/test_add_to_phase.py`), each change read against the surrounding code.
- Layering against AGENTS.md, the acceptance list, and the commit/sync path.

**What is good**
- **Layering is clean.** The write lives in `taskfiles.add_to_phase()` — the only module that touches `tasks/` — with `httpd.py` adding a thin `POST /api/phase/add` route and `board.html` holding the UI (`joinablePhases()`, `phaseSheet()`, `addToPhase()`). Dependencies flow left-to-right as the module map requires.
- **The write goes through the existing door.** `add_to_phase()` appends via `append_to_section()` (`manager/core/taskfiles.py:453`), so it commits under `BOARD_COMMIT_MOVES` as `board: 40 gained 51 (<name>)`, fires the sync commit hook, and creates a missing `## Cards` section rather than appending loose prose — the two edge-case acceptance items.
- **Guards are doubled, front and back.** The server refuses a phase outside `to-do/`, a card outside backlog/to-do, a non-phase target, a phase joining a phase (no nesting), a numberless card, a stale filename, and a path where a filename belongs — and `_phase_holding()` re-reads disk to refuse a card any phase already lists, which is exactly what makes the two-boards case produce two lines rather than a lost one. The frontend `joinablePhases()` gates the same states so the action never appears where the server would reject it, and is absent (not present-and-empty) when no phase waits in `to-do/`.
- **Tests are thorough** (32 cases): line shape and position, the empty- and missing-section cases, the on-disk re-read, every refusal, the commit gate on and off against a real throwaway git repo, plus `board.html` source invariants and a `node --check` parse. Nothing from the out-of-scope list was built.

**For a human reviewer to eyeball (non-blocking, nothing to act on)**
- The `⟶ phase` action opens the sheet on a single click with no arm-then-fire. This is deliberate and matches the drag-to-`done/` sheet the task pointed at (the named choice is the confirmation) — worth a glance only because the house rule is "actions that cost tokens or stop work arm," and this action does neither.
- The ticker summary reads `<phase>.md gained <n> — <title>`, i.e. the raw phase filename with its `.md` extension, which is slightly more machine-shaped than neighbouring ticker lines. Cosmetic.

**One thing you should know:** I could not execute the suite myself — this environment's sandbox denied the worktree checkout and the test invocation — so my confidence rests on reading the code and the test file plus the work report's passing count, not on a run I watched.
