# 28 — A pinned port stays pinned: fix the TIME_WAIT hop that rewrites .env

**Status:** Review
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


---

## Work report — 2026-08-01 07:35 (Juno)

The work is committed on `task/28-a-pinned-port-stays-pinned` and the full suite passes (636 tests, `python3 -m unittest discover -s tests`, the project's whole definition of done).

## Closing report

**Committed and verified: a restart no longer hops off its pinned port, and a hop that is genuinely warranted now says what it did to `manager/local/.env`.** One commit, `6829136`, touching `start.sh`, `AGENTS.md`, `tests/test_pinned_port.py` (new) and one anchor line in `tests/test_install_first_boot.py`.

What changed:

- **The root cause.** `start.sh`'s `is_free()` probe now sets `SO_REUSEADDR` before binding, matching the board's own `ThreadingHTTPServer` (`manager/core/board.py:51`, which binds `127.0.0.1` with reuse-address on). A socket the just-stopped board left in `TIME_WAIT` no longer reads as occupied, so the reported 26074 → 26075 hop cannot happen.
- **The belt.** A port that still reads busy gets a brief wait before the walk — five seconds by default, `BOARD_PORT_WAIT` in start.sh's environment overrides it — re-asking `is_our_board` on each beat, so a board that finishes coming up during the wait is opened rather than duplicated. `is_our_board` still short-circuits first, before any probe.
- **The message.** The hop still persists the new port (hooks and agents read `BOARD_PORT` and must reach the live board), but now prints three lines: that the port is held and which port it took, `Rewriting BOARD_PORT in manager/local/.env: <old> → <new>`, and `To reclaim <old>: free it, then set BOARD_PORT=<old> in manager/local/.env.` The old text misnamed the file as `manager/.env`; so did `AGENTS.md`, and both are fixed.
- A non-numeric `BOARD_PORT_WAIT` falls back to the default instead of killing the start on `set -e`.

How it was verified — `tests/test_pinned_port.py` runs the real `start.sh` as a subprocess against a scratch host whose `board.py` is a stub recording the port it was launched with, over real sockets: a genuine `TIME_WAIT` remnant (produced by closing the server end of an actual connection first) leaves the pin and the `.env` file untouched; a listener that lets go 1.5s in keeps the pin; a listener that does not is waited on, then walked past, with the `.env` rewritten and the rest of its keys and comments intact; our own board answering short-circuits to "already running" without launching the board or touching `.env`. Each `TIME_WAIT` test first asserts that a plain bind *does* refuse that port and skips otherwise, so it can never pass on a platform where the bug does not reproduce — that assertion is what pins old behaviour against new.

For a reviewer, in order:

- `start.sh:44-58` (the probe) and `start.sh:78-92` (the wait loop) — the whole behaviour change.
- `tests/test_pinned_port.py:130-146` — `leave_time_wait` / `plain_bind_refuses`, the pair that makes the regression test real rather than vacuous.
- `tests/test_install_first_boot.py:374` — the pre-existing test that extracts the persist snippet out of `start.sh` was anchored on the old `"Persisting BOARD_PORT"` wording; it is now anchored on the heredoc invocation instead. Same assertions, no coverage lost.

One thing to know, not to act on: `BOARD_PORT_WAIT` is read from the environment only, not from `manager/local/.env`, so it is deliberately absent from `manager/core/.env.example` — adding it there would advertise a `.env` key that nothing reads (the settings reference page on the site is generated from that file). It is documented in the `AGENTS.md` "Seeing the board" section as an environment variable.
