/* Arm-then-fire, one state machine (task 125, WP16).

   Anything that costs tokens, stops work or writes a decision arms on
   the first click and fires on the second. The page redraws by
   demolition, so the truth cannot live on a button: it lives here, in a
   store keyed by a stable name, and a rebuilt button reads it back. One
   timeout per phase:

     rest --arm--> armed --fire--> busy --settle--> rest
                    | after armMs     | after busyMs
                    v                 v
                   rest              rest (and the caller is told: no answer)

   An armed window that runs out goes back to rest by itself — a second
   click after it is a first click again, never a fire nobody confirmed.
   A busy window that runs out is the caller's to report. Nothing here
   retries anything. */

const ARM_MS = 5000;
const BUSY_MS = 15000;

function arming({ store = {}, now = () => Date.now(), later = (fn, ms) => setTimeout(fn, ms),
                  armMs = ARM_MS, busyMs = BUSY_MS } = {}) {

  /* the phase of `key` now: 'rest', 'armed' or 'busy' — an expired
     window reads (and is filed as) rest */
  function phase(key) {
    const st = store[key];
    if (!st) return 'rest';
    if (st.until <= now()) { delete store[key]; return 'rest'; }
    return st.phase;
  }

  function remaining(key) {
    const st = store[key];
    return st && st.until > now() ? st.until - now() : 0;
  }

  /* the first click: armed for `ms`, then rest again unless it fired */
  function arm(key, ms = armMs, onExpire) {
    const until = now() + ms;
    store[key] = { phase: 'armed', until };
    later(() => {
      const st = store[key];
      if (st && st.phase === 'armed' && st.until === until) {
        delete store[key];
        if (onExpire) onExpire();
      }
    }, ms);
    return until;
  }

  /* the second click: busy until settled, or until `busyMs` runs out —
     then `onTimeout` is told, once, and the key is at rest */
  function fire(key, onTimeout) {
    const until = now() + busyMs;
    store[key] = { phase: 'busy', until };
    later(() => {
      const st = store[key];
      if (st && st.phase === 'busy' && st.until === until) {
        delete store[key];
        if (onTimeout) onTimeout();
      }
    }, busyMs);
    return until;
  }

  /* the answer arrived: back to rest. False when it had already timed out. */
  function settle(key) {
    if ((store[key] || {}).phase !== 'busy') return false;
    delete store[key];
    return true;
  }

  function disarm(key) { delete store[key]; }

  /* one click: arms when at rest, says 'fire' when armed, and 'busy'
     while a fire is out */
  function click(key, ms = armMs, onExpire) {
    const p = phase(key);
    if (p === 'busy') return 'busy';
    if (p === 'armed') return 'fire';
    arm(key, ms, onExpire);
    return 'armed';
  }

  /* drop every key the predicate no longer keeps */
  function sweep(keep) {
    for (const key of Object.keys(store)) if (!keep(key)) delete store[key];
  }

  return { arm, click, disarm, fire, phase, remaining, settle, store, sweep };
}

/* Read-only: is `key` armed in `store` now? For a view that draws the
   armed wording and holds no instance of its own. */
function isArmed(store, key, at = Date.now()) {
  const st = (store || {})[key];
  return !!st && st.phase === 'armed' && st.until > at;
}

/* Read-only: the phase of `key` in `store` now — 'rest', 'armed' or
   'busy' — without filing an expired window. For a view that keys its
   drawing on it (task 130). */
function phaseOf(store, key, at = Date.now()) {
  const st = (store || {})[key];
  return st && st.until > at ? st.phase : 'rest';
}

export {
  ARM_MS, arming, BUSY_MS, isArmed, phaseOf
};
