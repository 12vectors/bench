# 48 — A phase is a card that lists its cards

**Status:** To Do
**Assignee:** istos
**Priority:** High — everything else about phases reads this; get the
shape wrong and three cards inherit it
**Type:** Feature

A phase is a group of related tasks meant to run one after another. Rather
than a new directory, a new stage or a registry, a phase is **a task card
like any other** — `**Type:** Phase`, with a `## Cards` section naming its
members in the order they run. This card is the model and its rendering:
after it, the board knows what a phase is and shows it. Nothing runs yet.

## Context

- `tasks/` holds only state and has to keep working as a plain folder
  kanban even if `manager/` is deleted. A phase that lived anywhere else
  would be a second source of truth; as a card it is diffable,
  greppable, reviewable and syncs like everything else.
- **`Type` already exists and is already read.** `TYPE_RE`
  (`manager/core/taskfiles.py:27`) matches any value and `read_task`
  surfaces it (`:70`), so `**Type:** Phase` needs no parser change to be
  stored — only somewhere to mean something.
- **`Depends on` is documented but parsed nowhere.** `AGENTS.md`
  describes it as informational — "the board does not enforce it; it
  informs whoever picks the next card" — and a grep of `manager/core/`
  finds no reader. This card gives it one.
- Ordering *within* a lane is task `../backlog/25-order-within-a-lane.md`
  and is not needed here: a phase's order comes from its own list, not
  from where its cards happen to sit.

**Affected areas:** `manager/core/taskfiles.py` (reading the section and
the dependency line), and `manager/core/board.html` (the chip on a member
card).

## What to build

- **`**Type:** Phase`** marks a card as a phase. The name of the phase is
  the card itself — its number and title — so nothing new has to be
  named, spelled twice or kept in step.
- **A `## Cards` section** listing members in run order, one per line,
  each beginning with a task number:

  ```markdown
  ## Cards

  - 31 — Stand up site/ and its build
  - 32 — Serve it from a Cloudflare Worker
  - 33 — The landing page
  ```

  Document order is run order. The number is what is parsed; the title
  after it is for the reader and is never matched against anything.
- **One direction only.** The phase card lists its members; member cards
  say nothing about phases. Membership therefore cannot disagree with
  itself, and there is exactly one place to edit when it changes.
- **`Depends on` is parsed at last, and it guards rather than orders.**
  The list says what runs next; a member's dependencies say whether it
  *may* — a card whose dependency is not finished is not startable even
  if the list reached it. Expose it on the task; do not act on it here.
- **Membership is derived for the member card.** The board reads every
  task file already, so a member's phase and its position are computed
  from the phase cards rather than stored twice.
- **A `⟶ <phase> 3/5` chip** in the member card's footer row, in the dim
  register beside `CI` and `PR ↗` — a destination, not a status — opening
  the phase card.
- **Drift is flagged, not swallowed**, in the same spirit as `status
  drift`: a listed number with no card, the same card listed by two
  phases, a card listed twice by one phase. Each is an authoring mistake
  that would otherwise surface much later as a runner behaving oddly.

**Out of scope** — tempting neighbours left alone:

- Running anything. No branch, no launches, no advancing — card 49.
- The header chip and the run/hold actions — card 50.
- The board action that adds a card to a phase — card 51.
- Nesting. A phase card is not a member of another phase.

## Acceptance

- [ ] Given a card with `**Type:** Phase` and a `## Cards` section, when
      the board reads it, then its members and their order are available
      in the order the section lists them.
- [ ] A member card shows `⟶ <phase> <n>/<total>` in its footer row, and
      clicking it opens the phase card.
- [ ] A card in no phase shows no chip and is unchanged in every way.
- [ ] Given a `## Cards` line naming a number no card has, the board
      flags it rather than silently skipping it.
- [ ] Given the same card listed by two phase cards, both are flagged.
- [ ] `Depends on` is parsed into a list of numbers and exposed on the
      task; nothing yet acts on it.
- [ ] Edge case: a `## Cards` section that is empty, or a phase card with
      no such section, reads as a phase with no members rather than
      failing.
- [ ] Edge case: a number in the list written as `#31` or `31 —` or bare
      `31` all resolve to the same card.

## Notes

The phase card earns its keep beyond membership: it is where the phase's
own brief lives (what this group of work is for), it is what the board
will later hang a branch and a PR on, and it is what makes the whole
feature reviewable — a phase arrives in the repo as a file you can read
in a diff before anything runs.

Most phase cards will be written whole, with their members already
listed and each member's `Depends on` already filled in, rather than
assembled on the board. The authoring path (51) is the convenience, not
the primary one — which is a good reason for the file format to be
pleasant to write by hand.
