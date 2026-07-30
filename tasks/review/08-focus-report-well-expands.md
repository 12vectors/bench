# 08 — Let the agent's report open from the Focus view

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/4
**Priority:** Medium — the report is the payoff of a whole agent run, and Focus dead-ends exactly there
**Type:** Bug

When an agent finishes, Focus's "Right now" well shows "‹name›'s report
on ‹task›" with the `›` chevron that everywhere else on the board means
"click to expand" — but the well is a static div: no handler, and the
report text it advertises is never rendered. The one moment Focus has
something a human must actually read, it can't show it.

## Context

- `manager/core/agents.py:55-56` — the closing event: summary
  "‹name›'s report on ‹task›", `detail` = the full report text. The data
  reaches the browser; nothing is missing server-side.
- `manager/core/board.html:1450-1454` — Focus renders the last event as
  `<div class="well">› ‹summary›</div>`: `detail` is dropped, no click
  handler, yet the `.lead` chevron matches the visual language of real
  disclosures. The affordance is decorative exactly where it looks most
  functional.
- `manager/core/board.html:1404` — the Sessions timeline already does
  this right: any event with `detail` gets `fold(key, 'the report' |
  'plan steps' | 'output', ev.detail)` — an expandable section. So the
  fix has an in-house pattern to reuse, and the two views should not
  behave differently for the same event.
- Affected areas: `board.html` Focus renderer only; no server change.

## What to build

- In the Focus well, when the rendered event carries `detail`, make the
  well a real disclosure: click toggles the full text, reusing the
  existing `fold()` mechanism (or its CSS/behaviour) rather than
  inventing a second expand pattern. Report text is machine-adjacent
  prose — render it in the mono/preformatted style the Sessions fold
  already uses, scrollable if long.
- When the event has no `detail` (the common live case: "running:
  pytest…"), the well stays as it is — but then it should not wear the
  disclosure chevron. One glyph, one meaning: `›` only when clicking
  does something. (Check the other `.well` call sites — board.html:860,
  893 — for the same lie.)
- Expansion state should survive the SSE-driven re-renders Focus gets
  every few seconds while a session is live — same discipline the
  Sessions folds already need (keyed state, not DOM-only state).

## Acceptance

- [ ] After an agent finishes, clicking the report line in Focus opens
      the full report text in place; clicking again closes it.
- [ ] The open state survives the periodic re-render (it does not snap
      shut on the next SSE event).
- [ ] Wells without expandable content no longer show the `›`
      disclosure glyph — visual grammar audit of all `.well` call
      sites.
- [ ] The Sessions timeline's existing fold behaviour is unchanged.

## Open questions

- None.

## Notes

Found trying to read Cleo's closing report on card 02 from Focus — the
report that (per card 05) contained the uncommitted-work warning a
human genuinely needed to see. The same text was reachable all along
via Sessions → the run's timeline → "the report" fold; this card makes
the obvious path work too. Related in spirit to
`06-agent-reports-written-for-humans.md`: 06 makes reports worth
reading, this makes them reachable where people actually look.


---

## Work report — 2026-07-30 07:00 (Skye)

Committed and verified. Here is the closing report.

---

The work is complete and committed on `task/08-focus-report-well-expands` as `5261b55`, with the full test suite (34 tests, including 5 new ones) passing via `python3 -m unittest discover -s tests -v`. All changes are in `manager/core/board.html` plus a new test file; no server-side code was touched, as the task predicted.

What changed, in three parts:

- **The Focus well is now a real disclosure.** In `renderFocus` (manager/core/board.html:1457), when the rendered event carries `detail` — the agent's closing report — the well is rendered as a `<details class="fold wellfold">` whose `<summary>` is the well itself. Clicking toggles the full report, shown in the fold's existing mono `<pre>` styling with a 300px scroll bound for long reports. The `›` lead rotates 90° when open, taking the place of the standard fold's `▸`/`▾` prefix.
- **Open state is keyed, not DOM-only.** The fold carries a `data-key` (`w` + the event's timestamp) checked against `S.openFolds` on every render, and `renderFocus` re-attaches toggle listeners after rewriting `#cgrid` — the same discipline `renderTimeline` already applies to `#ftl`, so the report stays open across the SSE-driven re-renders while a session is live.
- **The chevron audit.** Wells where clicking does nothing no longer wear `›`: the board cards' live activity line (both the summary and "warming up" variants, formerly board.html:860/864), the no-detail Focus well, and the "no activity yet" placeholder now lead with a neutral `·`. The drive wells' `✳` was left alone — that glyph means "drive", not "expand". The Sessions timeline's `fold()` mechanism is unchanged.

How it was verified: `tests/test_focus_well.py` encodes the acceptance criteria as source-level invariants — every `›` worn by a well lead must sit inside the wellfold summary, static wells must use a neutral glyph, the detail must render in a `<pre>` with a scroll bound, the fold must be keyed into `S.openFolds` with listeners re-attached, and the Sessions fold code must remain intact. One honest caveat: this repo has no JS test runner or browser harness, so the click-to-toggle behaviour itself was desk-checked, not exercised — the tests pin the structure that makes it work.

For the reviewer:

- Look first at the `renderFocus` branch at manager/core/board.html:1457–1473 and the listener re-attach at manager/core/board.html:1544 — that pair is the whole fix.
- If you want to see it live, open the Focus view on a session whose agent has finished: the report line should expand in place and stay open while events keep arriving.
