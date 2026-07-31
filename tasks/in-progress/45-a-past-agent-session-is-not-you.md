# 45 — A past agent session is labelled "You", because "You" is what the board says when it does not know

**Status:** In Progress
**Assignee:** istos
**Priority:** High — the flight recorder misattributes work, and
misattribution is worse than an absence: the list looks complete
**Type:** Bug

Sessions shows agent names only for agents this board process launched.
Every session read back from disk loses its agent id, and the label falls
through to `You · <id>` — so past agent runs do not vanish from the list,
they appear as the human's own sessions. What looks like "several sessions
of me and no old agents" is one bug: the old agents *are* the several
sessions of me.

## Context

The label is decided in one place, `manager/core/events.py:27`:

```python
def session_label(meta: dict) -> str:
    agent_id = meta.get("agentId") or ""
    if agent_id:
        record = state.AGENTS.get(agent_id) or {}
        ...
        return f"{who} · #{num.group(1)}" if num else who
    return f"You · {meta['id'][:8]}"
```

**"You" is not a positive identification. It is the `else`.** Anything the
board cannot attribute to an agent it attributes to the person.

That is fine while the board is up. `ingest_event` (`:41`) reads
`raw["agent"]` — the adapter puts `BOARD_AGENT_ID` there
(`adapters/claude/emit.py:194`, from the child env set in
`agents.py:138`) — and stores it on the session meta, so a live run reads
`Reed`, `Juno`, `Basil`.

It breaks at the disk boundary, in two steps:

1. **The agent id is never persisted.** The `event` dict built in
   `ingest_event` (`:47-59`) carries `ts`, `session`, `kind`, `summary`
   and optionally `file` / `cmd` / `detail` / `ok`. The agent id is read
   into the *session registry* and never copied into the *event*, and the
   event is what `state.persist()` writes. Verified on this repo's own
   state: every persisted event's keys are
   `['kind', 'session', 'summary', 'ts']`, and of 63 files in
   `manager/local/state/sessions/`, the only one containing the string
   `"agent"` is `board.jsonl` — a different log.
2. **So the replay cannot recover it.** `load_disk_sessions()` (`:96`)
   rebuilds each session's meta with `"agentId": None` hardcoded. It is
   not an oversight in that function — the data is not on disk to read.
   `session_label()` then takes the `else`.

The evidence, from this checkout while the card was written. Two rows in
Sessions read `You`, and their last persisted event is:

```
be5408af…  kind: report   "Nell's report on 41-the-drawer-renders-…"
d1a65161…  kind: report   "Piper's report on 35-the-site-reads-on-a-phone.md"
```

Nell and Piper are agents. Their sessions are wearing the user's label,
and carrying their own closing reports underneath it.

A third thing compounds it: `state.AGENTS` is memory-only
(`state.py:22`), so even with the id restored, the *name* would be gone
after a restart — the best today's fallback could say is `Agent · #41`.
The same gap is why a replayed session wears no model chip
(`board.html:1575` resolves it through the live agent list).

**Affected areas:** `manager/core/events.py` (event shape, replay,
labelling) and `manager/core/state.py` (what a session's identity is and
where it lives).

## What to build

- **Persist who a session belonged to.** The smallest fix is one more key
  on the persisted event; the better one is a per-session identity record
  written once when the session is first linked — `agentId`, the agent's
  name, its model, its task — because the name and model are the parts a
  restart loses and neither can be recovered from an event stream.
- **Read it back in `load_disk_sessions()`**, so a replayed session
  arrives with the same label it had while it ran.
- **Stop asserting "You" when the board does not know.** After the fix,
  absence of an agent id on a *live* session still means the person — a
  human Claude Code session genuinely sends none. For a session replayed
  from an old file that predates persistence, absence means unknown, and
  the label should say something neutral rather than claim it was you.
  Old logs must not be retro-attributed, in either direction.
- **Keep the fallbacks honest and distinct.** `Agent` when the id is
  known and the name is not; the name when the name was persisted;
  neutral when nothing was recorded. Three different states, three
  different words.
- **Let the model chip follow.** If the model is persisted with the
  identity, a replayed session can wear its chip instead of the board
  saying nothing rather than guessing — that rule was written for a board
  that had nothing to read.

**Out of scope** — tempting neighbours left alone:

- Making `state.AGENTS` a durable registry of every run ever launched.
  This card needs a session to remember who it was, not the board to
  remember every agent.
- The Sessions list's ordering, filters or timeline rendering.
- `board.jsonl` and the board event log, which are a separate stream.
- Retro-labelling the sessions already on disk. They have no id to
  recover; a migration that guesses from `"<Name>'s report on …"` would
  be a heuristic in the one place that should not have one.

## Acceptance

- [ ] Given an agent session that ran before the current board process,
      when Sessions is opened, then the row carries the agent's name and
      task — not `You`.
- [ ] Given a human session, when it is replayed from disk, then it still
      reads `You` — this fix does not relabel the person's own sessions.
- [ ] Given a session file written before this change (no identity
      recorded), when it loads, then it is labelled neutrally and no row
      claims to be you.
- [ ] Restarting the board does not change any session's label.
- [ ] A replayed agent session shows its model chip, or the board keeps
      saying nothing — but the same way every time, not by accident of
      what happens to be in memory.
- [ ] Edge case: a session whose events arrived before its agent id did
      (the id is attached on a later event) is labelled correctly after
      the fact, as it is today.

## Notes

The design principle this breaks is one the project states elsewhere: the
board says nothing rather than guessing — that is why a session with no
known model wears no chip. Labelling an unattributable session `You` is
the opposite of that rule, and it is load-bearing here because the
Sessions view is a flight recorder. A gap in it is obvious; a confident
wrong name is not.

**Risks** — session files are append-only JSONL read by
`load_disk_sessions()` with a bare `json.JSONDecodeError` guard. Whatever
shape the identity takes, a file written by the old code and a file
written by the new one both have to load; a header line prepended to
existing files would break every reader that assumes line one is an
event.
