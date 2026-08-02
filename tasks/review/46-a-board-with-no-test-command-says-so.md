# 46 — A board whose agents cannot run anything says so

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/53
**Assignee:** istos
**Priority:** Medium — the failure is silent until an agent has already
spent a run finding out
**Type:** Feature

`BOARD_AGENT_COMMANDS` is the one setting a headless agent cannot work
around: a test runner missing from it is a test the work agent cannot
run. Since the install stopped asking for it, a project the detector does
not recognise starts with it **empty** — correctly, because a wrong
default is worse — and nothing on the board mentions that until a run
ends with an agent explaining it could not verify its work.

## Context

- `install.py` detects the runner from the project (`TEST_COMMANDS`:
  `package.json` → `npm test`, `Cargo.toml` → `cargo test`, `go.mod` →
  `go test ./...`, `pyproject.toml`/`setup.py`/`tests/` → `python3 -m
  unittest`). A project matching none of them gets an empty value rather
  than the example's Python default.
- `manager/core/.env.example` documents the consequence where the key is
  defined — but that file is read by whoever goes looking, which is
  exactly who does not need telling.
- The board already has a vocabulary for "a thing this project has not
  set up": the **⛭ drive** chip says so and its tooltip explains what to
  create when `local/driver/start` is absent
  (`config.driver_path()`, surfaced as `hasDriver` in
  `httpd.state_payload()`). This is the same shape of problem and should
  borrow that shape.
- The agent's own report is the current channel, and it is the wrong one:
  it arrives after a worktree, a branch, a full model run and a card
  moved to `review/` — and it arrives as prose a reader has to notice.

**Affected areas:** `manager/core/config.py` (the value is already read
there), `manager/core/httpd.py` (the state payload), and
`manager/core/board.html` (wherever it is shown).

## What to build

- **Surface the empty state once, where a person will see it.** The
  header is where the board already says something is not converging
  (the sync chip) and where the drive's absence is explained. One quiet
  indicator, not a toast and not a banner — this is a configuration
  fact, not an alarm.
- **Say what to do.** The message names the setting and the file:
  `BOARD_AGENT_COMMANDS` in `manager/local/.env`. A reader who has never
  opened that file should be able to fix this without opening the docs.
- **Only when it is actually empty.** A project that set it — by
  detection or by hand — shows nothing at all. This must not become
  another permanent chip.
- **Consider the launch, not just the header.** The sharper version is
  to say it at the moment it matters: a work launch on a board with no
  runnable commands is a launch whose agent cannot check its own work.
  Whether that is a note in the ticker at launch, or something the
  **▸ start work** action says, is a design decision this card should
  settle rather than assume. It should not *block* the launch: an agent
  that only edits files is still useful, and bench does not refuse work
  because a project is unconfigured.

**Out of scope** — tempting neighbours left alone:

- Changing detection. `install.py`'s table is the other half of this and
  it is deliberately conservative; adding markers to it is its own card.
- Guessing a command at board start, or writing to `.env` from the
  board. The board reads settings; it does not author them.
- The definition-of-done checks (`manager/local/checks`), which are a
  different list with a different job.

## Acceptance

- [ ] Given `BOARD_AGENT_COMMANDS` empty, when the board is opened, then
      it says so once, naming the setting and the file that holds it.
- [ ] Given the setting has a value, nothing about it appears anywhere.
- [ ] Setting it and restarting the board clears the indicator.
- [ ] The indicator is not an alarm colour: nothing is failing, something
      is unconfigured — the design system reserves `--alarm` for blocked,
      failed or HIGH.
- [ ] Edge case: a value of only whitespace, or a lone comma, counts as
      empty — `split_commands()` in the adapter's `hook_settings.py`
      already treats it that way, and the board must agree.
- [ ] With the setting empty, a work launch still runs; the agent simply
      has no project commands.

## Notes

This is the cost of the trade made when the install stopped asking (see
`install.py`'s `TEST_COMMANDS`): the old question guaranteed an answer by
demanding one from someone who might not have it, and detection
guarantees only a *right* answer when it recognises the project. Empty is
the honest outcome for everything else — but honest and invisible is
still a board that lets an agent discover the problem on the user's
behalf, slowly.


---

## Work report — 2026-08-02 13:43 (Reed)

Implemented and committed on `task/46-a-board-with-no-test-command-says-so` (commit `64f3e2b`); the full suite passes (1050 tests, `python3 -m unittest discover -s tests`), including 19 new cases in `tests/test_no_agent_commands.py`.

**What changed**

An empty `BOARD_AGENT_COMMANDS` is now said in two places, neither of which blocks anything:

- **The header, standing still.** `manager/core/httpd.py` sends `hasAgentCommands` beside the existing `hasDriver`, and `manager/core/board.html` grows a `#cmdchip` next to the sync chip: a `no agent commands` chip in the settled register (`--idle` dot, `--muted` text, the setting name in mono), whose tooltip names `manager/local/.env` and gives an example value. It is drawn only when the server says the setting is empty — a payload that does not mention it (an older server, the first frame) shows nothing rather than guessing.
- **The launch, at the moment it matters.** This is the design decision the card left open: it is a **note in the ticker**, appended to the launch's own line, rather than anything on the `▸ start work` action. It fires for the two intents that would have run the commands — work (`start_agent`, which phase members go through too) and act-pr (`start_pr_fix`) — and not for read-only kinds, which never had those commands. The launch itself is untouched: the card still runs, commits and lands in `review/`.
- **One answer to "what is empty".** `config.agent_commands()` splits the setting the way the adapters' standalone `split_commands()` does, so whitespace and a lone comma are nothing configured everywhere. A test asserts core and both shipped adapters agree over the same table of inputs.
- `AGENTS.md` (the install-detection passage) and `manager/core/.env.example` now describe the indicator where the trade that created it is already explained.

**To know**

- The chip's visible text names the setting; the file that holds it is in the tooltip, following the "no driver" chip's precedent of explaining the fix on hover.
- Nothing writes to `.env` and nothing guesses a command — the board still only reads settings.

**Worth a reviewer's first look**

- `manager/core/board.html:1070` — `renderAgentCommands()`, its wording and colour choice (no `--alarm`, no `--accent`).
- `manager/core/agents.py:257` — `_no_commands_note()` and its two call sites, if you disagree that the ticker (rather than the action) is the right channel at launch.


---

## PR review — 2026-08-02 16:52 (Sage)

PR REVIEW: APPROVE

The work is committed on `task/46-a-board-with-no-test-command-says-so` (commit `64f3e2b`) and ready to merge; it does what task 46 asked and stays in scope. I reviewed the full diff and read the surrounding code in context.

**What it does**
An empty `BOARD_AGENT_COMMANDS` is now announced in two non-blocking channels: a quiet `no agent commands` header chip (next to the sync chip) and a note appended to the launch's own ticker line for the two intents that would have run the commands (`▸ start work` and `↻ act on PR`).

**What I checked and found good**
- `config.py:agent_commands()` splits the setting exactly as both shipped adapters' `split_commands()` do, so whitespace and a lone comma count as empty everywhere — the edge case the acceptance list names. A test asserts core and both adapters agree over one input table.
- `httpd.py` sends `hasAgentCommands` beside `hasDriver` — the same "thing this project hasn't set up" shape the task said to borrow. Layering is respected (httpd and agents read down into config; no upward dependency).
- `board.html:renderAgentCommands()` draws only when `S.state.hasAgentCommands === false`, so a payload missing the key (older server, first frame) shows nothing rather than guessing. Colour is `--idle` dot / `--muted` text — settled register, never `--alarm`.
- `agents.py:_no_commands_note()` returns `None` when configured; fires for `start_agent`/`start_pr_fix` and not read-only kinds. `base_note` is initialised to `None` (agents.py:322) so the new append loop is safe, and `name` is bound (agents.py:654) before the modified summary in `start_pr_fix`.
- The launch is never refused — note only.
- Docs (AGENTS.md, `.env.example`) describe the indicator where the trade is already explained; nothing writes to `.env`. Out-of-scope neighbours left alone.

**To know (for the human deciding this card)**
- I could not run the test suite in this review session — the permission sandbox blocked executing it. I verified correctness by inspection; the work report claims all 1050 tests pass, including 19 new cases in `tests/test_no_agent_commands.py` (splitter, payload, header chip run under node, launch behaviour, docs). Worth a local `python3 -m unittest discover -s tests` before merge if you want the confirmation I couldn't produce here.
- Minor judgement call: `--idle` (the design system's "done" driftwood) is reused for "unconfigured". It satisfies the not-alarm requirement and reads as neutral, but if you want a token that doesn't overload "done" that's the one thing to eyeball.
- The header chip names the setting in visible text and the file (`manager/local/.env`) only on hover, mirroring the existing "no driver" chip; the file is named inline in the launch ticker note, so the "name the setting and the file" acceptance is met across the two channels rather than in the chip alone.
