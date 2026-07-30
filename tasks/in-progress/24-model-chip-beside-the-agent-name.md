# 24 — A model chip beside every agent name

**Status:** In Progress
**Assignee:** istos
**Priority:** Low — the data is already there; this is putting it where eyes are
**Type:** Feature

Task 12 made the board record the model each launch used, but it
surfaces only at the tail of the session-detail metadata line —
"… · task/04-… · claude-opus-4-8" — where nobody's eye lands. With
per-intent models configured, which brain did the work becomes a real
question ("was that review sonnet or opus?"), and the answer should
sit beside the name that did it: a small chip, everywhere an agent
wears its name.

## Context

- Task 12 (landed) records the resolved model (or inherited) on the
  session record; the Sessions detail line proves the data flows.
- Where agent names appear today: the sessions list entries
  ("Olive · a83fae45"), the session-detail header, the Focus view's
  header, the card face's agent line while working ("Cleo · 7m"),
  and header session chips on the Board view.
- Design system: machine-produced text is Plex Mono; the session-id
  hash beside the name ("a83fae45") is exactly the visual register
  this chip belongs in — dim, mono, small. Colour means state, and a
  model is not a state: the chip must stay neutral.

**Affected areas:** `board.html` only; possibly the state payload if
any of the name-bearing views lacks the model field today.

## What to build

- One chip component: the model string in small mono on the dim
  neutral used by the id hash, rendered immediately after the agent's
  name wherever that name identifies a run — sessions list, session
  header, Focus header, the card's live agent line.
- Display the resolved model as recorded, shortened for the chip by
  dropping the redundant vendor prefix (`claude-opus-4-8` → chip
  `opus-4-8`, full string on hover/title). Inherited runs wear the
  resolved name too if known; if the launch genuinely didn't know
  (pre-12 sessions replayed from disk), the chip is absent, not a
  guess.
- Old persisted sessions without the field render exactly as today —
  no chip, no placeholder.

**Out of scope** — tempting neighbours left alone:

- Recording anything new (12 did that); no per-event model tracking.
- The ticker line — one-line narration stays terse; the chip lives
  where names are headings, not prose.
- Any colour-coding of models.

## Acceptance

- [ ] Sessions list, session-detail header, Focus header and the
      working card's agent line all show the chip beside the name for
      a fresh launch; hover reveals the full model string.
- [ ] A pre-existing session file without the model field renders
      chip-less and un-broken.
- [ ] The chip is visually the id-hash register (mono, dim), adds no
      new colour meaning, and doesn't shift the layouts it joins —
      names and timestamps stay aligned.

## Open questions

- None.

## Notes

Prompted (2026-07-30) by reading a session where the model sat at the
end of the metadata line: with work/review models diverging per task
12's intent split, "which model did this" is now a routine review
question, and the answer was hiding.
