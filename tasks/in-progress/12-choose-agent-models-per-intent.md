# 12 — Let the board choose agent models, per launch intent

**Status:** In Progress
**Priority:** Medium — works today by inheritance, but invisibly and uncontrollably
**Type:** Feature

Bench never specifies a model: headless agents run on whatever the
vendor's own resolution produces (the claude CLI falls through to the
user's `~/.claude/settings.json`; opencode to its global config). That
is a sane default but a bad ceiling — the choice is invisible on the
board, varies by machine, and can be hijacked silently by an
environment variable the board happens to inherit. And one model for
everything ignores that the intents differ: work agents write code;
relevance checks and PR reviews are read-and-judge jobs that could ride
a cheaper, faster model.

## Context

- No file in `manager/core/` mentions a model (verified by grep across
  `config.py`, `agents.py`, both adapters' `run`, `.env.example`).
- `agents.py::_launch` builds the agent env on top of the board
  process's environment, so e.g. `ANTHROPIC_MODEL` set in the shell
  that ran `start.sh` flows through to every claude agent with nothing
  on the board saying so.
- The delivery path for a setting like this already exists and was just
  rebuilt by `../done/05-agent-permission-modes-forbid-the-agents-own-contract.md`:
  `.env` setting → `config.py` → `_launch` env → adapter renders it
  natively (exactly how `AGENT_MODE` and `AGENT_COMMANDS` travel).
- Three-layer law: core carries the model name as an opaque string —
  it never validates or interprets it; what names are meaningful is
  vendor knowledge, so validation (or non-validation) belongs to the
  adapter/vendor.
- Affected areas: `config.py`, `agents.py`, both adapters' `run`,
  `.env.example`, `adapters/README.md`; optionally the sessions/focus
  UI for display.

## What to build

- Settings, defaults-empty (empty = inherit vendor default, today's
  behaviour exactly):
  - `BOARD_AGENT_MODEL` — one model for all headless launches.
  - `BOARD_AGENT_MODEL_WORK` / `_ACT_PR` / `_REVIEW` — per-intent
    overrides beating the general one. Review covers both PR review
    and relevance checks (they share the review intent).
- `config.py` resolves intent → model string; `_launch` passes it as
  `AGENT_MODEL` in the env. Empty means the variable is simply absent —
  adapters must not receive an empty flag value.
- Claude adapter: append `--model "$AGENT_MODEL"` when set. Opencode
  adapter: set the model key in the generated config when set (verify
  the current config key/format against opencode docs at build time,
  as with the permission block).
- Contract: document `AGENT_MODEL` in `adapters/README.md` as optional
  env in — absent = vendor default, value = opaque vendor-native model
  name each adapter passes through untranslated.
- Honesty in the UI, minimal version: record the resolved model (or
  "inherited") on the agent's session record so Sessions/Focus can show
  what a run actually used. No board-side model picker — this is
  configuration, not a per-launch decision.

Out of scope: model routing logic (cost caps, fallbacks, retry on a
different model), any attempt to normalize model names across vendors,
and per-task model selection.

## Acceptance

- [ ] With nothing set, launches are byte-identical to today (no
      `--model` flag, no model key in the opencode config) — verified
      by the stub-binary tests.
- [ ] `BOARD_AGENT_MODEL=x` reaches a claude launch as `--model x` and
      an opencode launch as the model key; a per-intent variable beats
      it for that intent only.
- [ ] Review-intent launches (PR review and relevance) pick up
      `_REVIEW`; work and act-pr pick up theirs.
- [ ] `.env.example` documents all four settings with the
      inherit-by-default behaviour; `adapters/README.md` documents
      `AGENT_MODEL`.
- [ ] A session's record shows the model the launch was given (or that
      it inherited).

## Open questions

- None.

## Notes

Origin: the owner asked "what models are the agents using and where is
that defined" and the honest answer was "nowhere, and it depends whose
machine the board runs on" — with every agent so far silently riding
the owner's personal CLI default. Inheritance stays the default;
this card makes it a choice instead of an accident.
