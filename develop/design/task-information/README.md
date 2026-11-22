# Task information for a busy reader (task 149)

The design behind the card detail view's hierarchy: who reads a card, the
questions they bring, where each class of information lives, how loud it
is, and how the reader reaches the detail behind it. It was written in
the same run as the implementation and is reviewed with it in the PR.

- `README.md` — this file: the reader, the map, the summary contract and
  the representative screens.
- `walkthrough.md` — a scenario-based script for an independent reviewer.
- `answers-template.md` — where that reviewer records answers and
  confusion.
- Fixtures: `tests/fixtures/task-information/*.md` (the cards) and
  `readings.json` (what the server computes from them).
- Captures: `verify/browser_cases/case_task_information.py` writes the
  after screens (names below) beside every `bench-browser` run's evidence.

## The reader and their four questions

A software architect with five minutes and twelve cards. They open a card
to decide one thing — start it, review it, merge it, send it back, or
leave it — and they ask, in this order:

1. **What is the work, and why?** The problem, the intended change and
   its boundaries — not the title again.
2. **What is required?** To *do* the work (inputs, dependencies, the
   criteria it will be judged against) and, separately, to *merge* it
   (the merge requirements this board enforces).
3. **Where does it stand?** What is active, what is done, what failed,
   what is unverified — in that card's own records.
4. **What happens next, and who acts?** One action, one actor, or "nothing
   is yours to do".

The before screens (Ronald's captures of card #64, kept in the work
state's `reference/149-task-*-before.png`) answer question 4 first, then
spend most of the first screen on an optional demonstration, its history
of failed optional checks and four "where the change is" rows. Questions
1 and 2 are answered only in the Brief, after a run-on line of header
fields and several screens of instructions.

## Principles

- **The brief owns the requirement.** Nothing on the Overview is a second
  copy to keep in step: it quotes the brief and links to it.
- **One authority per fact.** Delivery status is the server's
  (`delivery.py`); acceptance and findings are `acceptance.py`'s; inputs
  are `inputs.py`'s; the outline is `taskfiles.brief_outline`. The page
  places them; it computes no readiness of its own.
- **Loud only when current.** A blocker, an open question, a failure on
  the current commit stays at the point of decision. Explanations of what
  a thing is, optional and historical material, and provenance fold.
- **Absence is said, not filled.** No lead paragraph reads "nothing to
  quote"; no report reads "result not stated"; nothing is inferred from a
  stage, a clean exit or a title.

## The information-distribution map

Priority: **P1** leads the Overview; **P2** is on the Overview, visible;
**P3** is on the Overview, folded or set back under *More about this
card*; **H** lives in its own tab, linked from the Overview.

Since task 186 the Overview leads with **Where it stands · what happens
next** and puts **The work** directly after it, then **What is
required**, the reviews and *More about this card*; nothing moved
between sections. The `task-info-status-first-*` screenshots show it.

| Information | Home | Priority | Reached by |
| --- | --- | --- | --- |
| Purpose, problem, scope, boundaries | Overview → **The work** (quoted excerpts) | P1 | "Read the full brief" → Brief |
| Open questions in the brief | Overview → The work (note) | P1 | Brief |
| The full requirement and its sections | **Brief** (field grid, section index, text) | H | tab, `?tab=brief`, the work's link |
| Delivery status, next action, actor | Overview → **Where it stands · what happens next** | P1 | More actions (same block) |
| A failed run | Overview → Where it stands (alarm block) | P1 | — |
| Progress of a running agent | Overview → Where it stands (agent-reported) | P2 | Activity, Focus |
| Last recorded result | Overview → Where it stands (report's first paragraph, labelled as the run's account) | P2 | Activity (whole report) |
| Orchestration scope and waiting decisions | Overview → Where it stands | P2 | Orchestrator view |
| Work base: which code the work starts from, and a held new start (task 150) | Overview → Where it stands → **Work base** (headline and why; "A new start is held" stays in view as a blocker) | P2 | Refs, evidence and history folded under it; ⌖ chip |
| Inputs and tools readiness | Overview → **What is required** → *to do the work* | P2 | Inputs tab |
| Dependencies | Overview → What is required → *to do the work* (number, stage) | P2 | `/cards/<n>` links |
| Acceptance criteria and their record | Overview → What is required (count) | P2 | Acceptance tab |
| Merge requirements (required only) | Overview → What is required → *to merge it* | P2 | ⚖ sheet |
| Optional checks, earlier runs, technical details | Under the merge requirements | P3 (folded) | ◫ evidence |
| Review findings, last review round, relevance verdict | Overview → **Reviews and findings** | P2 | Reviews tab (open findings and newest round first) |
| Show the result (demonstrations) | More about this card | P3 (one line and a fold when Off with nothing kept) | — |
| Where the change is (merged, installed, deployed) | More about this card → Show the result | P3 | — |
| Stack, Signals (Bench checks, CI, Bench/GitHub review) | More about this card | P3 | ⌖, ◫, ⚖ chips |
| Card identity and the view controls (copy link, expand/narrow, close) | The sticky head: `#n`, the status word and three icon buttons on one row, the title on the full width under them | P1 | Escape closes; each icon has its name as a tooltip |
| Every tool and action | **Tools**, once, at the top of the detail view — under the tab row, above every tab's text (owner review) | P1 (on every tab) | the card's ⋯ menu |
| Branch and plan | More about this card (folded) | P3 | ⌖ chip |
| Discussion, refinement | Brief tab | H | — |
| Explanations of what a section is and is not | A fold in that section | P3 | — |
| Dated history: progress, reports, reviews, decisions | **Activity** | H | Overview links |

Tab responsibilities after the change:

| Tab | Responsibility | What changed |
| --- | --- | --- |
| Overview | Answer the four questions, then hold everything else, set back | Reordered by the questions; added The work, Last result, dependencies, "to do / to merge" |
| Brief | The requirement, and the conversation that shapes it | Field grid instead of a run-on line; section index |
| Inputs | What a run will be given, and whether it can start | An empty card is one sentence; caveats folded |
| Acceptance | Criteria and their record | Untracked criteria lead with their first clause |
| Reviews | Findings and rounds | PR reviews (findings, newest round) before relevance reviews; explanations folded |
| Activity | The dated record | Unchanged; the Overview links into it |

### The owner's visual review (supersedes "Tools at the bottom")

Ronald inspected the candidate at `4ab11487d80d` and asked for three
changes, which this map now follows (his before screenshots are
`149-visual-feedback-tabs-spacing.png`, `-duplicate-tools.png` and
`-header-wrap.png` in the work state's `reference/`):

1. **A deliberate gap under the tab row.** Whatever comes first under the
   head's border — the tools, or the open tab's text — starts at least
   20px below it (the body's 14px gap plus 10px under the sticky head),
   on every tab, in the narrow and expanded views and in both themes.
2. **Tools at the top, labelled once.** The card's tool group (built by
   the card's own builder, so it cannot disagree with the ⋯ menu) sits
   directly under the navigation and before the tab's narrative, on every
   tab, with its one "Tools" label and no heading over it. A card with no
   tools in its stage draws nothing there. Arming and confirmation are
   the tools' own, unchanged; a redraw replaces the group, never adds one,
   and a focused tool keeps its focus.
3. **Compact header controls.** Copy link, Expand/Narrow and Close are
   icon buttons, each with its name in words (`aria-label`) and as a
   tooltip; Expand/Narrow changes name, icon and `aria-pressed` with its
   state; Copy link still confirms in a toast; Escape still closes. The
   three are one group (`.dctl`) that never wraps, on the head's first row
   beside the card's number and status, so the title has the full width
   on the row under them and wraps rather than shrinking — at 390px and
   320px too.

The browser case holds all three (`test_owner_review_*` in
`case_task_information.py`) and writes
`task-info-owner-header-<width>x<height>-<light|dark>.png` at 1280×900,
390×844 and 320×720, plus `task-info-owner-header-expanded-1280x900-*.png`.
Narrow widths there are viewport emulation, not browser zoom; the real
200% zoom is `case_card_detail`'s. Those captures are for a person to look
at: the run that made this change could not open them.

## The summary: provenance and freshness

**Where it comes from.** `taskfiles.brief_outline(text)`, computed from
the card's text on every `GET /api/card` and sent as `outline`. It quotes
the brief's opening paragraph, the first paragraph of its problem section
(`Context`, `Problem`, `Background`, `Why`, `Motivation`), the `###`
headings — or else the leading clause of each bullet — of its scope
section (`What to build`, `Scope`, `Approach`, …) and its boundaries
(`Out of scope`, `Non-goals`, or the seed template's inline `**Out of
scope**`). Excerpts are verbatim apart from Markdown punctuation, clipped
at a sentence boundary (420 characters for the lead, 320 for the others,
six scope items) and marked when they continue. HTML comments, fences,
tables, quotes, header fields and board markers are never quoted.

**Why nothing is persisted.** A stored summary would need an author, an
update rule and a staleness check; the brief already has all three. So
the summary has no owner but the brief: to change it, edit the brief's
opening paragraph. No model is asked on a page read (or ever): a brief
with nothing quotable reads *"nothing to quote"*.

**Freshness.** The outline names the brief revision it was taken from
(`rev`, the first 12 hex digits of `brief_sha`). It arrives in the same
reply as the text the Brief tab shows, so the two cannot disagree. While
the board lists a newer card revision than the text in hand, the
Overview says the summary is not current and dims it. History — work
reports, reviews, discussion — is outside the brief by `card_parts`, so
an old work report never becomes the requirement.

**Legacy cards.** A card written before any of this gets the same
treatment: a lead paragraph and `## Context` are the norm in this
board's history. Cards with none (a title and a checklist) say so.

**The last result** is the same kind of excerpt: the first prose
paragraph of the newest `Work report` or `PR update`
(`acceptance.reports(...)[i].headline`), labelled *the run's own account,
not a check result*. A card in review or done with none reads *"No work
report is on record on this card, so its result is not stated here."*

## Representative screens

### Overview, before (card #64 at 1280×900 — `149-task-overview-before.png`)

```text
#64 Initialize work directories only at the installation mode's…  [Delivered] [Copy link] [Expand] [Close]
Overview  Brief  Inputs  Acceptance  Reviews  Activity
┃ DELIVERED
┃ Delivered at 2c6aa445c49e.
┃ nothing is yours to do here right now
Merge requirements · No merge requirements apply
  No merge requirements are configured on this board. …
▸ Optional checks and earlier runs (4)
▸ Technical details
┌ Show the result  (Off) ───────────────────────────────────────────┐
│ No demonstration was asked for. …                                  │
│ ┆ Would screenshots help you review this change? [Request] [Not now]│
│ What ran                                                           │
│  • bench-backend — failed at 2c6aa445c49e (another commit — history)│
│  • bench-backend-proposed — failed …   • bench-docs — stale …      │
│ Where the change is: Implemented no · Merged no · Installed unknown │
│ ▸ Request a demonstration   ▸ What a demonstration is, and is not  │
└────────────────────────────────────────────────────────────────────┘
Stage · Progress · Acceptance · Findings · Last review          ← first facts, below the fold
```

### Overview, after (the long legacy fixture at 1280×900 — `task-info-164-legacy-long-overview-1280x900.png`)

```text
#164 [Delivered]                                                            [🔗] [⤢] [✕]
Initialize work directories only at the installation mode's work root
Overview  Brief  Inputs  Acceptance  Reviews  Activity
──────────────────────────────────────────────────────────────────────────────────────
                                                         ← a deliberate gap under the line
Tools
[◔ still true?] [⚿ profile] [◫ evidence] [⌖ plan] [PR ↗] …
──────────────────────────────────────────────────────────────────────────────────────
THE WORK
  Make first installation layout-aware: initialize tasks/, plans/ and reference/
  only at the resolved work root, retain vendored defaults, and clearly …
  Problem       External-workspace support and execution plans are merged, …
                …continues in the brief — from "Context"
  Scope         • Installation-mode-aware initialization
                • Preservation, reinstall and mode changes
                • Legacy installations             — from "What to build"
  Not in scope  Changing how the default work root is resolved; …
  Quoted from the brief as it reads now (revision 8517f25594ea). … Read the full brief
WHERE IT STANDS · WHAT HAPPENS NEXT
┃ DELIVERED  Delivered at <commit>.  nothing is yours to do here right now
  Stage        Done · held by istos
  Progress     The latest run finished … (history, not current)
  Last result  Committed on task/164-… and tested: install.py now creates …
               — Work report by Juno, 2026-11-09 18:02: the run's own account, not a check result
WHAT IS REQUIRED
  To do the work   Information · Acceptance  8 criteria in the brief · Acceptance results not recorded
  To merge it      Merge requirements · No merge requirements apply   ▸ Optional checks…  ▸ Technical details
REVIEWS AND FINDINGS
  Findings     No findings recorded against the reviews.
  Last review  Reed approved at 2c6aa445c49e, 2026-11-10 12:14 · open this review
MORE ABOUT THIS CARD
  Show the result (Off) — No demonstration was asked for and none is kept on this board.
  ▸ Screenshots, the checks bench ran, and where the change is
  Signals …   ▸ Branch and plan
```

(`[🔗] [⤢] [✕]` stand for the three icon buttons — drawn as inline SVG
on the page, each named "Copy a link to this card", "Expand the reading
view" / "Narrow the reading view" and "Close the detail view".)

### Overview, after, at 390×844 (`task-info-204-review-findings-overview-390x844-light.png`, `-dark.png`)

```text
#204 [Changes requested]  [🔗][⤢][✕]
Show the time zone beside every
timestamp in the Activity view
Overview Brief Inputs Acceptance
Reviews Activity
──────────────────────────────────
                       ← the gap
Tools
[↻ act on PR] [◔ review PR] [⚿] …
──────────────────────────────────
THE WORK
 Activity shows local times without
 saying which zone they are in, …
 Problem
  Reports filed by runs carry UTC …
 Scope
  • Every Activity timestamp names its zone
  • Hovering or focusing a timestamp …
  • Sorting uses the recorded instant, …
WHERE IT STANDS · WHAT HAPPENS NEXT
┃ CHANGES REQUESTED
┃ <the server's sentence>
┃ [ the primary action, full width ]
┃ next · you
 Last result  Committed and tested on task/204 …
WHAT IS REQUIRED …
REVIEWS AND FINDINGS
 Last review  Olive asked for changes at 7d1e0aa94c21 …
```

The 390px layout stacks the facts' labels above their values (the
existing `dfacts` rule) and the primary action goes full width; nothing
scrolls sideways at 390px or at 320px (`case_task_information` holds
both).

### Brief, before and after (`149-task-brief-opening-before.png` → `task-info-164-legacy-long-brief-1280x900.png`)

```text
before: Status: Done PR: https://… Assignee: istos Priority: Medium — ambiguous … Type: Feature
after:  ┌──────────────────────────────────────────────┐
        │ Status    Done                               │
        │ PR        https://github.com/example/…/80    │
        │ Assignee  istos                              │
        │ Priority  Medium — ambiguous duplicate …     │
        │ Type      Feature                            │
        └──────────────────────────────────────────────┘
        Context · What to build · Acceptance · Out of scope      ← index; focus moves to the heading
```

### Inputs, before and after (`149-task-inputs-before.png` → `task-info-164-legacy-long-inputs-1280x900.png`)

```text
before: Inputs and required tools (Ready) / No supporting inputs… / [Recheck information] No check has run…
        Information readiness is one thing a launch checks. … / This card has no "Inputs and required tools"
        section. … / Inputs: No inputs. / Tools: No tools are listed. / ▸ What this does and does not prove
after:  Inputs and required tools (Ready) / No supporting inputs… / [Recheck information] No check has run…
        This card has no "Inputs and required tools" section, and its brief names no supporting input
        or tool — there is nothing to list.
        ▸ What this does and does not prove      ← the readiness caveat lives here now
```

### Reviews, before and after (`149-task-reviews-before.png` → `task-info-204-review-findings-reviews-1280x900.png`)

```text
before: Relevance reviews 0 / two lines of explanation / "No relevance review…" / PR reviews 1 /
        explanation / counts / findings / Review rounds …
after:  PR reviews 1 / counts / open findings / ▸ Record a finding / Review rounds: the newest, open /
        ▸ Earlier rounds / ▸ What a PR review is, and is not
        Relevance reviews 0 / ▸ What a relevance review is / "No relevance review…"
```

## What is not proved here

Screenshots and the browser case show that the information is placed,
reachable and truthful on the fixtures. They do not show that an
unfamiliar reader understands it: that is the walkthrough's job, run by
an independent reviewer, and it is unassessed until someone records
answers in `answers-template.md`.
