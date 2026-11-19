/* Holding a repaint while a pointer is down (task 147).

   A view painted from a string replaces its controls whenever its markup
   changes. A click is two events, the button going down and coming up,
   and the browser dispatches `click` only to an element both of them
   reached: a repaint that lands between them — the focus leaving the
   previous control asks for one, a reading arriving asks for another —
   takes the pressed control out of the page, and the click is lost
   without a trace. One genuine click had to become two.

   So a press holds the view's repaint until the click it belongs to has
   been dispatched, and the release asks for the repaint it held. Only
   the pointer gesture is held: a keyboard activation has no press, and a
   handler that paints in answer to the click itself paints at once.

     down() -- held --> done()   the click arrived (or the press was cancelled)
                 |
                 +-- up() -- settleMs, no click (dragged off) --> released

   A press whose release is never reported (the pointer left the window
   mid-press, say) stops holding after `maxMs`, so the view can never be
   frozen by a gesture the page did not see end. Nothing here paints:
   `onRelease` is the caller's way to ask for the paint it held. */

const SETTLE_MS = 400;
const MAX_MS = 8000;

function pressHold({ now = () => Date.now(), later = (fn, ms) => setTimeout(fn, ms),
                     settleMs = SETTLE_MS, maxMs = MAX_MS, onRelease = null } = {}) {
  let since = 0;
  let turn = 0;

  function release() {
    if (!since) return;
    since = 0;
    turn++;
    if (onRelease) onRelease();
  }

  /* the button went down: hold from now */
  function down() {
    since = now() || 1;
    turn++;
  }

  /* the button came up: the click follows in the same gesture, or — for a
     press dragged off its control — never; then the hold lapses */
  function up() {
    if (!since) return;
    const mine = ++turn;
    later(() => { if (mine === turn) release(); }, settleMs);
  }

  /* is a repaint to be held now? */
  function held() {
    if (since && now() - since > maxMs) release();
    return !!since;
  }

  return { down, up, done: release, held };
}

export { MAX_MS, pressHold, SETTLE_MS };
