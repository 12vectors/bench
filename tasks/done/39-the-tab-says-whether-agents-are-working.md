# 39 — The browser tab says whether agents are working

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/32
**Assignee:** istos
**Priority:** Medium — the state you most want while the tab is *not* the
one you are looking at is the one the tab does not carry
**Type:** Feature

A running agent is visible on the board and nowhere else. The moment you
switch tabs — which is the normal thing to do while an agent works for
several minutes — the only thing bench tells you is `bench · bench`,
identical whether three agents are running or none. Put the answer in the
tab title, where a backgrounded window can still say it.

## Context

- `manager/core/board.html:862` — `renderTitle()` writes
  `S.state.project + ' · ' + VIEW_TITLES[S.view]` and nothing else. Its
  comment already carries the constraint this card has to respect: the
  project comes first because "tab truncation eats the tail, and the tail
  is the same in every bench tab". A narrow tab shows little more than
  the first few characters.
- The count is already computed one function earlier. `renderChip()`
  (`:810`) filters `S.state.agents` to `status === 'running'` and renders
  "N agents working" with the longest elapsed time. `/api/state` has
  carried `agents: agents.list_public()` since `httpd.py:35`, so no new
  data is needed — this is a second consumer of a value the page already
  has.
- The server renders the first-paint title (`httpd.py:57-61`) so the tab
  is right before any state arrives. That stays as it is; the indicator
  is a live thing and belongs to the live render.
- `renderChip()` also tracks `liveYou` — live sessions that are not
  agents, i.e. you, working. That is not what this card is about: the tab
  should answer "is something happening without me", not "am I here".

**Affected areas:** `manager/core/board.html` — `renderTitle()` and the
constant beside it. No server change.

## What to build

- A count-carrying prefix on the tab title while agents are running,
  ahead of the project name — the same reasoning that put the project
  first applies harder to this, since a truncated tab must still show it.
  Something in the register of the board's own mono chips rather than an
  emoji: the count and a mark, then the title as it is today.
- No agents running → the title is exactly what it is now, byte for byte.
  A quiet board should look untouched.
- The count is the same set the header chip counts, so the tab and the
  chip can never disagree.
- The indicator survives a view switch: Board, Sessions and Focus all
  carry it, since it describes the board, not the view.
- Write `document.title` only when the string actually changes. `render()`
  runs on every SSE frame, and a title assigned dozens of times a second
  is a needless thing to do to the browser.

**Out of scope** — tempting neighbours left alone:

- The favicon. A colour or dot on the mark would survive truncation
  better than any prefix, but it is a design decision about the logo task
  22 and 23 settled, and it wants the design project's answer rather than
  an invented one. See Notes.
- A failed run in the tab. Different signal, sharper stakes, its own card
  — see Notes.
- Notifications, sound, badging APIs, flashing the title. This is a
  passive indicator, not an interruption; bench's one interruption budget
  is spent on the toast a failed run already fires.
- The header chip, which is correct today and stays as it is.

## Acceptance

- [ ] Given one or more running agents, when the tab is backgrounded,
      then the tab title names how many — readable in a narrow tab, i.e.
      not after the project name.
- [ ] Given no running agents, the title is character-identical to
      today's `<project> · <view>`.
- [ ] The tab's count and the header chip's count always agree, because
      both read the same filtered list.
- [ ] Switching between Board, Sessions and Focus keeps the indicator and
      changes only the view word, as it does now.
- [ ] Given an agent that finishes, when its run ends, then the tab
      returns to the plain title without a reload.
- [ ] Edge case: exactly one agent reads as one, not "1 agents".
- [ ] `document.title` is assigned only on change — verifiable by
      watching it across a burst of SSE frames with nothing else moving.

## Notes

The one thing the tab already does well is identify *which* bench it is,
and that must survive: a second project's board is the whole reason the
project name leads. Anything added here is a prefix on a string that is
already fighting for room, so brevity is the design.

Two follow-ups this deliberately does not do, both worth their own cards:

- **The favicon.** It never truncates, so it is the strongest carrier of
  this signal — but the mark came from the design project (task 23), and
  a state-coloured variant should come from there too, not from a
  developer picking a dot colour.
- **A run that died.** The board treats a failed run as a state a card
  wears, with a toast, precisely because it must not be missed — and a
  toast fired at a backgrounded tab is missed by definition. That is a
  sharper version of this same gap.


---

## Work report — 2026-07-31 16:13 (Juno)

Committed on `task/39-the-tab-says-whether-agents-are-working` as `72a0d41`; the full suite (497 tests) passes.

**What changed**

- `manager/core/board.html` — `renderTitle()` now builds the tab title through a new pure `tabTitle(project, view, working)`. While agents run it reads `2◌ · <project> · <view>`; with none running it is character-identical to the previous `<project> · <view>`. The mark is `◌`, the in-flight glyph the CI and copilot chips already wear, so the prefix sits in the board's own mono register rather than being an emoji.
- Same file — the `status === 'running'` filter that `renderChip()` used inline is lifted into `runningAgents()`, and both the header chip and the tab title now count through it. That is what makes "the tab and the chip can never disagree" structural rather than coincidental.
- Same file — `document.title` is assigned only when the computed string differs from the last one written (a `shownTitle` cache), since `render()` fires on every SSE frame.
- `tests/test_tab_agents.py` (new) — behaviour run for real in node (skipped when node is absent): the plain title for a quiet board, the count leading for 1/2/3 agents, only `running` records counted, every view carrying it, a finished run returning the tab to the plain title, no write before state arrives, and a 20-frame burst producing exactly one write. Plus source-level wiring checks: one running-filter, one `document.title` writer, and nothing but the count ahead of the project.
- `tests/test_board_title.py` — its "one writer, starting with the project" invariant asserted the literal assignment began with `S.state.project`, which the prefix makes false. It now asserts the single writer builds its string via `tabTitle(S.state.project, …)`, with the ordering claim itself moved to the new file.
- `AGENTS.md` — the "Seeing the board" paragraph on the tab title gains the running-agent prefix.

**For the reviewer, in order**

1. `manager/core/board.html:868-897` — the new `tabTitle`/`renderTitle`, and whether `2◌ · ` is the right shape for the prefix. It is the one taste call in the change; the task specified "the count and a mark", and `◌` was picked because it is already bench's in-flight glyph.
2. The edit to `tests/test_board_title.py:129` — an existing assertion was deliberately loosened in one direction (the literal string no longer starts with the project) and its intent re-homed. Worth confirming that trade reads right.

Nothing here needs running by hand; the definition of done is `python3 -m unittest`, which passes. The out-of-scope neighbours the card named — the favicon and a failed run in the tab — are untouched.


---

## PR review — 2026-07-31 16:19 (Wren)

PR REVIEW: APPROVE

The work is complete, committed on `task/39-the-tab-says-whether-agents-are-working` (`72a0d41`), and ready to merge. My approval could not be posted via `gh pr review --approve` — GitHub refuses it because this checkout's git identity (`istos`) is the PR author — so the verdict is recorded as a PR comment (issuecomment-5143882620) instead. The verdict itself is unaffected.

**What this does.** The browser tab title now leads with `<n>◌ · ` while and only while agents are running, ahead of the project name, so a backgrounded/narrow tab still answers "is something happening without me". A quiet board shows the old `<project> · <view>` string byte-for-byte.

**What I checked (read the full diff; read the PR branch's `board.html` at every touch point):**
- Count leads the title — `tabTitle()` (`manager/core/board.html:888`) prefixes only when the count is truthy. Traced by hand: 0→`bench · bench`, 1→`1◌ · bench · bench`, 3→`3◌ · bench · bench`.
- One agent reads as `1◌`, not "1 agents" — count + mark, no pluralised word.
- Tab and header chip cannot disagree — the inline `status === 'running'` filter is lifted into `runningAgents()` (`board.html:823`) and both `renderChip()` and `renderTitle()` call it. The only other running reference (`agentOnTask`, a `.find` keyed on the task file, `board.html:718`) is a different question and correctly left alone.
- Write-on-change — `shownTitle` (`board.html:895`) caches the last string; `renderTitle()` returns early when unchanged, so an SSE burst does not thrash `document.title`.
- Survives view switch; returns to plain when the last agent finishes — no reload, since the prefix keys on the count, not the view.
- Mark is the board's own `◌` in-flight glyph (already used by the CI/copilot/starting chips), not an emoji.
- Scope — frontend-only, no server change as the task required; favicon, failed-run-in-tab and notifications untouched; the AGENTS.md "Seeing the board" paragraph updated to keep docs in step.
- Tests — new `tests/test_tab_agents.py` is thorough (node behaviour for 0/1/N, running-only counting, all three views, finish-to-plain, no write before state, 20-frame burst → one write, plus source-level wiring invariants). The existing `tests/test_board_title.py` assertion that the `document.title` literal "starts with the project" was correctly re-homed to "built via `tabTitle(S.state.project, …)`" — a reasonable loosening now that a prefix can precede the project.

**To know (not blocking):**
- I could not execute the suite in this read-only review session (python/node are sandbox-blocked here). The work report states the full 497-test suite passes, and the title logic is simple enough that I verified it by inspection and manual tracing. A human merging may want to run `python3 -m unittest` once to confirm.
- Merging is yours — the board never merges. The self-approval block above means the automated PR-poll won't see an APPROVE review from this identity; treat this comment as the verdict.

No changes required.
