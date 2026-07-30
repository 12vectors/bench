# 24 — A model chip beside every agent name

**Status:** Review
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


---

## Work report — 2026-07-30 12:04 (Olive)

The work is committed and the full suite passes.

---

Committed on `task/24-model-chip-beside-the-agent-name` as `053e1d7`; the whole suite (`python3 -m unittest`, 274 tests including 10 new ones) passes.

**What changed**

`manager/core/board.html` gains one chip component — `modelChip(agent)`, with a `shortModel()` helper — rendered wherever a name identifies a run: the sessions-list row, the session-detail header, the Focus header's reference line, and the working card's agent line. The chip is small mono text in `var(--dim)`, the same register as the session-id hash it sits next to, and takes no state colour. It shows the model shortened by dropping the redundant vendor prefix (`claude-opus-4-8` → `opus-4-8`, `anthropic/claude-opus-4-8` → `opus-4-8`) with the full recorded string on hover; a name the shortening rule doesn't recognise, such as `gpt-4o`, is shown exactly as recorded. A launch whose model was never known — an inherited vendor default, or a session replayed from disk after a board restart — renders no chip and no placeholder.

No server-side change was needed: the model already reaches the browser on the agent record, and the sessions list resolves it through the existing `agentFor()` lookup.

**One behaviour change beyond adding the chip, worth a reviewer's eye:** the session-detail metadata line and the Focus reference line used to end with the model string. Leaving that would have said the same thing twice in one header, so those two places now print only `model inherited`, and only when the model genuinely is unknown. Task 12's honesty about inheriting is preserved; the duplicated resolved name is gone.

`AGENTS.md` gains a paragraph describing the chip next to the agent-names paragraph.

**Nothing is required of the reader to accept this card**, but two things are worth a look:

- `manager/core/board.html:105-114` — the `.mchip` rules, including the per-site font sizes that track each context's id-hash size, and the card-only ellipsis bound so a long model name squeezes rather than widens the card's row.
- `tests/test_model_chip.py` — the behaviour half lifts `esc`, `shortModel` and `modelChip` out of the page and runs them in node, skipping cleanly where node is absent (GitHub's `ubuntu-latest` runners have it). The placement half stays source-level, in the house style, since `board.html` has no frontend test runner.

I did not verify this in a running board — the change is presentation-only and the render sites are exercised as source invariants rather than in a browser. If you want a visual check before merging, the ⛭ drive chip on the review card launches this worktree's board.
