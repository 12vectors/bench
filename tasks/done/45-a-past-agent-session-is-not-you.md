# 45 — A past agent session is labelled "You", because "You" is what the board says when it does not know

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/35
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


---

## Work report — 2026-07-31 16:35 (Milo)

Committed on `task/45-a-past-agent-session-is-not-you`, full suite green.

---

The work is **done, committed and verified**: `python3 -m unittest discover -s tests` passes 498 tests, 15 of them new in `tests/test_session_identity.py`. One commit, `f428ee0`.

**What changed**

A session's identity is now persisted beside its event log as `manager/local/state/sessions/<id>.who.json` — agent id, the agent's name, the model it rode, and the task. A separate whole file rather than an extra key on the events, for the reason the card's Risks section names: the logs are append-only JSONL whose first line every reader takes for an event. It is also the only workable place for the name and the model, since neither appears anywhere in the event stream — `state.AGENTS` is memory-only, so a restart was losing them.

- `manager/core/state.py` — `persist_identity()` / `read_identity()`, and the `IDENTITY_SUFFIX` constant. `read_identity()` returns `None` (not `{}`) when nothing was recorded, because "recorded as no agent" and "we do not know" are the two states the label must not blur.
- `manager/core/events.py` — `ingest_event()` keeps the agent's name and model on the session meta and writes the sidecar, but only when what the board knows changes (so a late-arriving agent id rewrites it and a hundred identical events do not). `load_disk_sessions()` reads it back. `session_label()` now has three registers: the agent's name, `You` only for a session positively known to have carried no agent, and a neutral `Session · <id>` otherwise.
- `manager/core/board.html` — `agentFor()` falls back to the persisted identity when the live launch record is gone, so a replayed agent session wears its model chip. Things that need liveness (the Hold button, the worktree branch) find nothing on that fallback and stay silent, exactly as they did before.
- `AGENTS.md` — the Sessions bullet now describes the sidecar and the three labels; it also had a stale path (`.sessions/*.jsonl`), corrected to `local/state/sessions/`.

**For the reviewer, in order**

1. `manager/core/events.py:33` — `session_label()`, the three-register rule, is the heart of the fix.
2. `manager/core/board.html:716` — `agentFor()` now returns two shapes; check the call sites at lines 1595 and 1754 agree that the replayed shape is allowed to be thinner.
3. The 63 existing session logs in this checkout have no sidecar, so they will relabel from `You · …` to `Session · …` on the next board start. That is the intended outcome — the card rules out retro-attribution — but it is the visible change on first restart.

**To know**

- `TodoWrite` is not available in this session's toolset, so the board had no live plan to display for this run. Nothing else was affected.
- The neutral word for an unattributable session is `Session`, rendered as `Session · <id8>`; if you would rather it read `Unknown`, it is one string in `session_label()` and two assertions in the new test file.
