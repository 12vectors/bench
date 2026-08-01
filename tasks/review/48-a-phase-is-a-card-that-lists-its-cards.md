# 48 — A phase is a card that lists its cards

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/40
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


---

## Work report — 2026-08-01 09:02 (Finch)

Committed on `task/48-a-phase-is-a-card-that-lists-its-cards` (9621437), full suite green.

**What changed**

The model and its rendering are in, and nothing runs. `**Type:** Phase` plus a `## Cards` section is the whole of it:

- `manager/core/taskfiles.py` — reads a phase card's `## Cards` section into an ordered list of numbers (`31`, `#31` and `031` are one card; an indented line is a member's own continuation, neither member nor mistake; the section ends at the next `##`). It also parses `**Depends on:**` at last, into `dependsOn` — task numbers only, prose preconditions left for the reader — and acts on it nowhere.
- `collect()` now calls a new `weave_phases()`, which resolves every phase card's list against the whole board: the phase gets `members` in document order, and each member gets `phase: {file, number, title, index, total}`. Membership is derived, never stored on the member.
- Drift is flagged on the card, next to `status drift`: a number no card has, a card two phase cards both list (both are flagged, and so is the member), a card one phase lists twice, plus two I added for the same reason — a line in `## Cards` naming no number, and a phase listing a phase (phases do not nest, so it is refused loudly rather than nested quietly).
- `manager/core/board.html` — a member card carries `⟶ <phase> 3/5` in the footer chip row beside `CI` and `PR ↗`; clicking it opens the phase card. Phase drift wears a `phase drift` pill in the top row. Chips can now take a leading glyph (`pre`), which is what the `⟶` uses.
- `AGENTS.md` — `Phase` added to the **Type** row and a new "A phase is a card that lists its cards" subsection under Task file format (it lands on the site's `/concepts/task-files/` page, which slices that section).

**Verification**

`python3 -m unittest` — 677 tests, all passing (672 before). New file `tests/test_phase_cards.py`, 31 tests: parsing and membership run through `taskfiles.collect()` over a throwaway `tasks/` directory — the same read the board does per request — covering every acceptance bullet including the empty-section and number-format edge cases; the chip and drift pill are source-level invariants on `board.html` in the style of `tests/test_card_actions.py`; `phaseLabel()` is lifted out of the page and run under node, as `test_drawer_markdown.py` does with `md()`. That class also parse-checks the page's inline script, since `board.html` has no runner of its own.

**Worth a look first**

- Two judgment calls that are mine, not the card's: a card two phases both list stays with the **first phase by number** (so the member's chip is not a coin toss, and the second phase does not count it as a member), and `dependsOn` is canonicalised the way member numbers are — `**Depends on:** 03, 05` reads as `["3", "5"]`, while a card's own `number` stays `"07"` as the filename spells it. Consumers must canonicalise; `taskfiles.canonical_number()` is the one place that does.
- The chip's label is the phase card's title with its leading `48 — ` stripped and clipped to 22 characters, with the full name in the tooltip (`manager/core/board.html:979`).
- No phase card exists in this repo yet, so the chip has been verified by tests and a parse check rather than by clicking a running board. Writing a real phase card is authoring, not this card's job — the first one written will exercise it.


---

## PR review — 2026-08-01 09:05 (Olive)

PR REVIEW: APPROVE

The work is committed on `task/48-a-phase-is-a-card-that-lists-its-cards` (9621437) and complete: the phase model and its rendering are in, nothing runs yet, and every acceptance bullet is satisfied. I read the full PR diff and the surrounding code; the tests read as correct but I could not execute them here (this review sandbox blocks `python3 -m unittest`), so the "677 green" claim rests on the work report — see the one action item below.

**To do (for whoever merges)**
- Run `python3 -m unittest` locally once before merge — the only check I couldn't perform myself.

**What I verified**
- **Scope is clean.** The PR changes exactly four files: `manager/core/taskfiles.py`, `manager/core/board.html`, `AGENTS.md`, `tests/test_phase_cards.py`. (`git diff main..branch` shows extra task-file churn, but that is local `main` trailing origin under board sync, not PR content — `gh pr diff` against the real base confirms the four.)
- **Parsing** (`_listed_cards`, `_depends_on`, `canonical_number`): `#31`, `031` and bare `31` all resolve to one card; `Depends on` takes only whole-number comma items and leaves prose preconditions for the reader; the `## Cards` section ends at the next `##` heading; empty or absent sections read as a phase with no members.
- **Derivation** (`weave_phases`): membership is computed across the whole board and never stored on the member. Drift is flagged, not swallowed, for every case the task named — an unknown number, a card listed twice by one phase, a card two phases both claim (both phases *and* the member flagged), a line naming no number, and a phase listing a phase (refused, not nested). A contested card deterministically stays with the first phase by number.
- **Rendering** (`board.html`): the `⟶ <phase> n/total` chip sits in the footer chip row via a new leading-glyph (`pre`) mechanism, opens the phase card through `findTask`/`showDetail` with `stopPropagation`, and phase drift wears a `phase drift` pill beside `status drift`.
- **Layering and out-of-scope**: derivation runs over already-read in-memory tasks inside `taskfiles.py`; new dict keys are additive, so no existing consumer breaks. Nothing runs, no header chip, no run/hold or add-to-phase actions, no nesting — all correctly deferred to cards 49–51.

**Minor, non-blocking (worth a human's awareness)**
- `CARDS_SECTION_RE`'s terminator matches only exactly `## ` — a `### Subheading` placed inside a `## Cards` section would not end it. Unlikely in practice.
- Indented bullet sub-items under a member line are silently treated as continuations and skipped (documented intent), so a nested `  - 32` would not count as a member.
