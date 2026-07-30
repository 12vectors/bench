# 17 — Make card actions click-solid: stable targets, instant feedback

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/13
**Priority:** Medium — the actions are the board's hands; every launch goes through this wobble
**Type:** Bug

The hover actions (▸ start work, ‖ hold, ◔ still true?, …) are easy to
miss and mute when hit. Two root causes, both in `board.html`: the
click target is small and *changes geometry mid-interaction* (appears
on hover with a slide animation, then the arm step swaps the label to a
different-width string under the cursor), and firing gives zero
feedback until the server's SSE update arrives a second or more later —
so the click reads as "did nothing", inviting a double-fire the server
then refuses.

## Context

- `board.html:176-183` — `.hoveracts button`: `padding:2px 8px`,
  11px font → ~20px hit target.
- `board.html:170-171` — the slot is `display:none` until `.card:hover`
  and enters with `animation:rise .12s` — the target is moving as the
  pointer arrives.
- `board.html:976-985` — the arm handler replaces the label with
  `act.confirm` ("start work" → "start it?"): different text width, so
  the button reshapes between first and second click; a missed second
  click bubbles to the card and opens the sheet. The 3.5s disarm timer
  means a slow second click silently re-arms instead of firing.
- `act.run()` (same block) fires the POST with no state change; the
  card redraws only when the server's SSE lands (worktree + spawn ≈
  seconds for start-work). Double-clicks in the gap hit the server's
  one-agent-per-task refusal and surface as an error toast for a click
  the user thinks failed.
- Design-system vocabulary already exists for the fix: breathe = agent
  alive, `--alarm` = armed, and actions already own the pill's slot.

**Affected areas:** `board.html` only (CSS + the action-slot builder);
no server change — the server's refusals stay as the backstop, they
just stop being reachable by honest clicking.

## What to build

- **Stable geometry.** Reserve each button's width for the wider of
  label/confirm (measure both, fix `min-width`), so arming restyles but
  never reshapes. Hit target to ≥24px tall (padding, not font size).
  Drop the entry animation or animate opacity only — the target must
  not travel while the pointer approaches it.
- **Unmissable armed state.** Keep the alarm colour, add the remaining
  window visibly (a subtle draining underline/边 on the 3.5s timer —
  and lengthen it to ~5s). A second click after expiry re-arms, which
  is correct, but the user must *see* the state they are clicking in.
- **Instant busy state on fire.** The moment `act.run()` is called the
  button locks: disabled, breathe animation, label → present participle
  ("starting…", "holding…", "checking…"), clicks swallowed. It stays
  locked until the SSE redraw replaces the card, or an N-second
  timeout restores it with an error toast naming the action — never a
  silent revert.
- **Misses fall harmlessly.** While the slot is visible, clicks in its
  padding/gap zone must not bubble into card selection — pad the slot's
  hitbox, stopPropagation at the slot, not per-button.

**Out of scope** — tempting neighbours left alone:

- Changing the arm-then-fire model itself (it guards token spend and is
  right); this card makes its states legible, not optional.
- Server-side idempotency/debouncing of launch requests.
- Touch support, keyboard activation of hover actions.

## Acceptance

- [ ] Given an unarmed action, when clicked twice briskly, then exactly
      one launch happens and the button visibly walked unarmed → armed
      → busy with no geometry change throughout.
- [ ] The armed button shows its remaining window; after expiry it
      reads as unarmed again (visual state, not just internal class).
- [ ] Given a fired action, when the server takes seconds, then the
      button reads busy immediately (breathe, participle label) and
      resolves into the card's new state without a flash of the old.
- [ ] Edge case — server error or timeout on fire: button returns to
      rest and a toast names the failed action; no stuck busy state.
- [ ] Edge case — click landing in the slot but between buttons:
      nothing happens; the card sheet does not open.
- [ ] All existing actions (start work, hold, back, reopen, still
      true?, review PR, act on PR, copilot, local commands) inherit the
      behaviour from the one slot builder — no per-action forks.

## Open questions

- None.

## Notes

Owner's report (2026-07-30): "easy to miss a click" and "takes a second
for bench to react so it's not clear if you clicked" — both reproduced
from the code paths above; the width-swap on arming is the likely main
miss-click culprit, and the feedback gap is structural (no optimistic
state at all between click and SSE).

**Risks**

- The busy state is client-side optimism: if the SSE connection is
  dead the timeout-and-toast path is the only honest exit — test it
  with the server stopped, not just slow.
- Local commands arm through a separate handler today
  (`board.html:961-965`); unifying into the slot builder must not
  change their arm/run contract.


---

## Work report — 2026-07-30 08:03 (Hazel)

 + action, because the board tears cards down and rebuilds them on every SSE render: a rebuild mid-window now re-applies the same armed or busy picture instead of silently disarming, and a second click keeps its meaning across a redraw.

- **Stable geometry.** Each button renders its rest, confirm and busy labels stacked in one CSS grid cell (`.actlbl`), so the button is born as wide as its widest state and arming restyles without reshaping. The slot now fades in with an opacity-only animation instead of sliding up, buttons are 24px tall, and the card's top row permanently reserves that height so hovering never grows the card or shifts its neighbours.
- **Visible armed window.** The window is lengthened from 3.5s to 5s and wears a draining underline animated over exactly that duration; a button rebuilt mid-window rejoins the drain partway through via a negative animation delay rather than restarting it.
- **Instant busy state.** Firing immediately disables the button, breathes the glyph in accent colour, and shows a present-participle label ("starting…", "holding…", "checking…", …). It unlocks when the SSE redraw replaces the card, when the request helper reports failure (they all now return success/failure), or when a 15s timeout restores it with an error toast naming the action. A thrown fetch (server down, dead SSE) is caught and toasted the same way — no silent revert, no stuck busy state.
- **Misses fall harmlessly.** The slot pads its own hitbox with padding cancelled by negative margin and swallows clicks itself (`stopPropagation` at the slot, not per button), so a click in the gap between buttons neither fires anything nor opens the card sheet.

**To do (reviewer):**
- Run `python3 -m unittest discover -s tests` to confirm the suite.
- Eyeball the arm→busy walk in a live board: hover an in-progress card, click "start work" twice briskly, and watch unarmed → armed (draining underline) → "starting…" with no width or position change.
- For the honest-exit path, stop the board server mid-session and fire an action: the button should return to rest after 15s with a toast naming the action.

**To know:**
- The commit deliberately does not change the arm-then-fire model, server-side idempotency, or touch/keyboard support — all named out of scope by the task.
- The local-command chips keep their exact arm/run contract (first click arms "run it?", second runs) but now inherit the drain bar, the busy lock and the 5s window from the shared machine; their old 3.5s bespoke handler is gone.
- Because the tests are source-level regex invariants (this repo's convention for `board.html`), they pin the contract — label ghost-stack, matching CSS/JS timer durations, slot-level click guard, `return res.ok` in every request helper — rather than executing the JS; the second "to do" item above is the behavioural check.
- One visual side effect worth knowing: every card's top row is 4px taller than before (20px → 24px), the price of a hit target that never moves.
