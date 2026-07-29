# Agent adapters

An adapter makes the task manager work with a particular coding agent.
Core never speaks any vendor's language — it launches jobs and ingests
normalized events; adapters translate at the edge. `claude/` ships as the
default. Select with `BOARD_AGENT_ADAPTER` in `local/.env`; a directory of
the same name under `local/adapters/` overrides the core one.

## The contract

An adapter is a directory with two executables:

### `run` — execute one headless job to completion

- env in: `AGENT_PROMPT` (the full prompt), `AGENT_MODE` (`work` = may
  mutate the checkout, `review` = read-only — map this intent to whatever
  permission mechanism your agent has), `AGENT_CWD`, and the `BOARD_*`
  passthrough (`BOARD_AGENT_ID`, `BOARD_TASK`, `BOARD_PORT`) which your
  event bridge must forward with every event.
- stdout is captured by the board as the job log. The prompts instruct the
  agent to end with marker lines (`NOT READY:`, `RELEVANCE REVIEW:`,
  `PR REVIEW:`, `ADDRESSED:`) — the board parses them from this output, so
  the agent's final text must reach stdout.
- exit 0 = completed; anything else = failed.

### `wire` — wire live-session visibility into the host project

Called by `install.py` with the project root as argv[1] (plus `--dry-run`).
Idempotently make the project's own interactive sessions report events —
however your platform allows (Claude Code: hooks; opencode: a plugin
subscribing to its event bus). Print a report; exit 0 on ok/fixed. If the
platform has no way to observe sessions, be a no-op with an honest message:
the board still runs headless jobs via `run`, you just lose the live
play-by-play.

### Events — the normalized schema (v1)

POST to `http://127.0.0.1:$BOARD_PORT/api/events`:

    {"v": 1, "session": str, "kind": str, "summary": str,
     "file"?: str, "cmd"?: str, "detail"?: str, "ok"?: bool,
     "running"?: bool, "agent"?: $BOARD_AGENT_ID, "task"?: $BOARD_TASK}

kinds: `session end idle edit read search command test check git plan
subagent web other`. `running: true` marks an in-flight action (shown as
the live line, not appended to the timeline); follow it with the completed
event. `kind: idle` = finished responding; `kind: end` = session over.
Classification happens in YOUR emitter — core never sees vendor payloads.

## Writing one (e.g. for opencode)

- `run`: `opencode run "$AGENT_PROMPT"` with its permission config mapped
  from `AGENT_MODE`; make sure the final output lands on stdout.
- `wire`: drop a plugin into the project that subscribes to tool events and
  POSTs the normalized schema with the `BOARD_*` env forwarded.
- Events beat perfection: start with `session`/`end` plus a generic
  `command` per tool call, refine kinds later.
