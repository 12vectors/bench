# 25 — Cards order within a lane: a Rank line the board writes on drop

**Status:** Backlog
**Priority:** Medium — a backlog that cannot say "this first" makes priority live in someone's head
**Type:** Feature

Lanes render in card-number order — creation order — so the backlog
cannot express "21 before 18" without a conversation. Add manual
ordering: drag a card up or down within its lane and the board records
the position in the card itself, because the directory-is-truth law
and the multi-user sync arc both demand that order live in the files
and merge one card at a time.

## Context

- Lane order today: filename sort, i.e. the immortal `NN-` creation
  number. The number is identity, not priority ("stays with the file
  for life") — reusing it for order is ruled out by the template's
  own contract, and index-file designs concentrate every reorder into
  one file, which under task 19's sync turns concurrent reorders into
  guaranteed conflicts. A header field in the card is the only shape
  that inherits everything the Status line already gets: watcher
  narration, `board:` commits, per-file merges.
- `taskfiles.py` already rewrites the Status line on move — the Rank
  write is the same kind of surgical header edit.
- The **Priority** field stays what it is: a label with a
  justification clause, not a sort key. Rank is where the human's
  actual sequence lives; the two answer different questions.

**Affected areas:** `taskfiles.py` (the Rank read/write),
`board.html` (in-lane drag targets, sort), `watch.py` narration of
rank changes, task-template comment (one line documenting the field
as board-managed).

## What to build

- `**Rank:** <integer>` in the card header, optional. Lane sort:
  rank ascending, then card number; unranked cards (every existing
  card) sort after ranked ones by number — today's boards render
  identically until someone reorders.
- In-lane drag: dropping between two cards writes the midpoint of
  their ranks to the ONE dragged card (sparse ranks — first ranks in
  a lane are 10, 20, 30…). When no integer gap remains between
  neighbours, renumber that lane's ranked cards in one `board:`
  commit and then place the card — rare, mechanical, visible in
  history.
- Stage moves leave Rank untouched (it goes stale-but-harmless in the
  new lane; the next reorder there re-ranks it). Nothing else is
  rewritten at move time.
- The ticker narrates reorders like moves ("18 ranked above 21 in
  backlog (you)"); under task 18's gate the write commits like any
  board edit.

**Out of scope** — tempting neighbours left alone:

- Auto-ordering by the Priority label, due dates, or any computed
  sort — rank is a human's hand, nothing else's.
- Cross-lane global priority; rank means nothing outside its lane.
- Reordering from the plain-folder view; `ls` keeps showing number
  order and that limitation is documented, not fixed.

## Acceptance

- [ ] Given an unranked lane, when a card is dragged above another,
      then exactly one file gains a Rank line, the lane renders in
      the new order, and a `board:` commit (when gated on) carries
      exactly that file.
- [ ] Given repeated bisection until the gap closes, then the board
      renumbers that lane in one commit and ordering behaviour is
      seamless to the user.
- [ ] A board with no Rank lines anywhere renders byte-identically to
      today; a card moved to a new stage keeps its Rank line and
      causes no misbehaviour there.
- [ ] Edge case — two synced boards reorder different cards in the
      same lane concurrently: both commits merge (different files),
      and both boards converge to the same order.

## Open questions

- None.

## Notes

Shape chosen over an index-file per lane and over filename prefixes —
reasoning preserved from the 2026-07-30 design discussion: the rank
must ride in the card so every ordering write is a single-file merge,
which is what task 19's git-as-lock-server model needs. Rank is
lane-local and deliberately allowed to go stale across moves; the
alternative (rewrite on every move) buys nothing but churn.

**Risks**

- Midpoint ranking with integers exhausts gaps after ~log2 insertions
  between the same pair — the renumber path is not an edge case to
  skimp on; test it deliberately, including its interaction with the
  single-file-per-commit expectation (the renumber is the one
  sanctioned multi-file board commit).
- Humans hand-editing Rank into nonsense (duplicates, negatives) must
  degrade to stable sort by number, never a broken lane.
