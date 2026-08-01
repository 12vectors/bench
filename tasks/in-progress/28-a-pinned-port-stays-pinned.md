# 28 — A pinned port stays pinned: fix the TIME_WAIT hop that rewrites .env

**Status:** In Progress
**Assignee:** istos
**Priority:** Medium — a routine stop/start silently moves a deliberately pinned port; found by the first downstream install within hours of 0.2
**Type:** Bug

Restarting the board can hop it off its own pinned port: start.sh's
free-port probe binds without `SO_REUSEADDR`, so the just-stopped
board's socket in TIME_WAIT reads as "taken by something else", the
script walks to the next port and — worse — persists the new number
over the user's explicit `BOARD_PORT` pin in `manager/local/.env`.
The user pinned 26074; one routine restart later they were on 26075
with their own setting overwritten. The probe also disagrees with the
server it probes for: `ThreadingHTTPServer` sets `allow_reuse_address`,
so the board itself could have bound the port the probe called busy.

## Context

- Field report (cicero-pas, first 0.2 restart): stop → start hopped
  26074 → 26075 with nothing actually listening; the hop message also
  misnames the file it rewrote ("manager/.env" — it is
  `manager/local/.env`).
- `start.sh` `is_free()`: a plain `socket.bind()` probe — fails on
  TIME_WAIT remnants that the actual server (reuse-address) would
  bind over fine. False "busy" → unnecessary hop.
- The persist behaviour exists for the *genuinely occupied* case, so
  hooks and agents follow the board (right and documented). The bug
  is hopping when nothing is there, and clobbering an explicit pin
  for a transient condition.

**Affected areas:** `start.sh` only (probe, retry, persist policy,
message wording).

## What to build

- Probe with `SO_REUSEADDR`, matching the server's own bind
  semantics — TIME_WAIT stops reading as occupied. This alone
  dissolves the reported race.
- Belt: if the pinned port still reads busy, distinguish "our own
  board" (existing is_our_board check answers) from "foreign": for
  foreign, briefly retry (a few seconds) before walking — restarts
  race their predecessor's shutdown more often than squatters exist.
- Persist policy: walking off a port the user explicitly pinned in
  local/.env still persists (hooks must follow the live board — the
  alternative is events into a dead port), but say it plainly: name
  the file correctly, state old → new, and print how to reclaim the
  pin. The message currently misnames the file; fix that regardless.

**Out of scope** — tempting neighbours left alone:

- A separate runtime port file with config-precedence changes — more
  moving parts than the bug warrants once the probe stops lying.
- Port management for drives/agents (they already read the same env).

## Acceptance

- [ ] Given a board stopped and immediately restarted on its pinned
      port, then it comes back on the same port with .env untouched —
      the TIME_WAIT case, tested with a real socket left in that
      state.
- [ ] Given the pinned port genuinely held by a foreign process, then
      start.sh retries briefly, then walks and persists, and the
      message names `manager/local/.env`, the old and new ports, and
      the reclaim step.
- [ ] `is_our_board` on the pinned port still short-circuits to
      "reopen the browser" — no probe, no hop.

## Open questions

- None.

## Notes

Diagnosis arrived nearly complete in the downstream report (TIME_WAIT
suspicion, the clobbered pin, the misnamed file) — second field
report, second time the reporter's analysis survives into the card
nearly verbatim. The probe/server asymmetry (`allow_reuse_address` on
one side only) is the one-line root cause.
