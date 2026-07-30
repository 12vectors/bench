# 12 — Let the board choose agent models, per launch intent

**Status:** Archived
**PR:** https://github.com/12vectors/bench/pull/10
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


---

## Work report — 2026-07-30 07:38 (Juno)

The work is complete and committed. Here is the closing report:

---

The task is implemented, tested and committed as a single commit (`9fed08d`) on `task/12-choose-agent-models-per-intent`; the full suite (87 tests, including 11 new ones) passes with `python3 -m unittest discover -s tests`, which is this project's entire definition of done.

**What changed**

- `manager/core/config.py` — four new defaults-empty settings (`BOARD_AGENT_MODEL` plus `_WORK`/`_ACT_PR`/`_REVIEW`) and an `agent_model(mode)` resolver: the per-intent setting wins, then the general one, and empty means inherit the vendor default. Core treats the name as an opaque string throughout.
- `manager/core/agents.py` — `_launch` puts the resolved model in the adapter env as `AGENT_MODEL` only when one is configured; when none is, it also *removes* any `AGENT_MODEL` the board process itself inherited, so a shell variable can no longer hijack launches silently. All four launch kinds store the resolved model (or `None` for inherited) on the agent record, exposed through `_agent_public`.
- `manager/core/adapters/claude/run` — appends `--model "$AGENT_MODEL"` when set (using the bash-3.2-safe empty-array idiom, since macOS's `/bin/bash` runs these scripts under `set -u`).
- `manager/core/adapters/opencode/permission_config.py` and `run` — the generated config gains a top-level `"model"` key when set. I verified the key and its `"provider/model-id"` format against opencode.ai/docs/config today, as the task asked.
- `manager/core/.env.example` and `manager/core/adapters/README.md` — the four settings and the `AGENT_MODEL` contract (optional env in; absent = vendor default; value = opaque, passed through untranslated, never sent empty) are documented.
- `manager/core/board.html` — the Sessions header line and the Focus reference line now show the model an agent run was given, or "model inherited", for exactly the honesty the task's Notes asked for. Interactive (human) sessions show nothing, since the board never launched them.
- `tests/test_agent_model.py` — new. Covers intent→model resolution in a fresh interpreter per env combination, the `_launch` env seam (including the stray-variable strip), the record field, and both adapters end-to-end via stub binaries, asserting launches with nothing set are argv/config-identical to before this change.

**For the reviewer**

- Start with `tests/test_agent_model.py` — the two `test_unset_launches_byte_identical_to_today` tests are the acceptance criterion that matters most, and the `LaunchEnv` class pins the "empty means the variable is simply absent" rule.
- One judgment call worth a look: `_launch` popping `AGENT_MODEL` from the inherited environment. The task's motivation section called out silent environment hijack for `ANTHROPIC_MODEL`; I stripped only the board's own contract variable (`AGENT_MODEL`), not arbitrary vendor variables, since filtering vendor env would be vendor knowledge core must not have.
