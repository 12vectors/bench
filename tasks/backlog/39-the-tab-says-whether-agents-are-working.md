# 39 — The browser tab says whether agents are working

**Status:** Backlog
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
