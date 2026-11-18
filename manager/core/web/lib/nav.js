/* The one place the page writes its address (task 134).

   A view changes the page's state the way it always did — `S.view`,
   `S.selected`, a tab, a session, a Settings section — and then says so
   with `syncRoute()`. This module asks the route source the composition
   root registered (`routeOf(S)`) where the page now is, and writes it:

   - **Once per gesture.** Calls in one turn of the event loop are
     coalesced into one write on a microtask, so "switch to the Board, then
     open the phase card" is one history entry, not two.
   - **Only when the destination changed.** An address equal to the one
     the bar already shows writes nothing, so a repaint, a stream frame, a
     reconnect or a periodic reading can call this a thousand times and
     add nothing to history.
   - **Never while an address is being applied.** Back, Forward and a
     link opened in this tab are applied under `holdRoute`, and nothing
     written from inside it reaches history — so applying an address can
     never push another and Back can never loop.
   - **Not before the page has applied its first address.** A direct entry
     is read first; until then the page's defaults are not a destination.

   A new destination is pushed; `{replace: true}` corrects the current
   entry instead (an alias read on entry, an archived card's one view).
   History entries carry the view a card was opened over, so Back to a
   card opened from Phases opens it over Phases again.

   Imports only lib/: it is the boundary, not a view. */
import { formatRoute } from './route.js';

let source = null;
let held = 0;
let ready = false;
let queued = null;     // {replace} while a write waits for its microtask
let writes = 0;

function setRouteSource(fn) { source = fn; }
function routeReady() { ready = true; }

/* Run `fn` with every sync inside it ignored: the page is being told where
   it is, not navigating. */
function holdRoute(fn) {
  held++;
  try { return fn(); } finally { held--; }
}

function here() {
  const loc = globalThis.location;
  return loc ? loc.pathname + loc.search : '';
}

function write() {
  const replace = !!(queued && queued.replace);
  queued = null;
  const hist = globalThis.history;
  if (!source || !hist) return;
  const route = source();
  const url = formatRoute(route);
  const state = { bench: 1, view: route.view };
  if (url === here()) {
    // the same address: only the remembered background may need noting
    if (!hist.state || hist.state.view !== route.view) hist.replaceState(state, '', url);
    return;
  }
  writes++;
  if (replace) hist.replaceState(state, '', url);
  else hist.pushState(state, '', url);
}

function syncRoute(opts = {}) {
  if (held || !ready || !source) return;
  if (queued) { queued.replace = queued.replace && !!opts.replace; return; }
  queued = { replace: !!opts.replace };
  const later = globalThis.queueMicrotask || ((fn) => Promise.resolve().then(fn));
  later(write);
}

/* Replace the current entry with `route`, now — for an address read on
   entry whose canonical form differs (`/` → `/board`). Not held: it is
   the correction of an entry that already exists, and adds none. */
function replaceRoute(route) {
  const hist = globalThis.history;
  if (!hist) return;
  const url = formatRoute(route);
  hist.replaceState({ bench: 1, view: route.view }, '', url);
}

/* A destination's whole address on the board this page was served by —
   never a hostname written down anywhere. */
function linkFor(route) {
  const loc = globalThis.location;
  return (loc ? loc.origin : '') + formatRoute(route);
}

/* Whether a click on a link is the page's to handle: the primary button,
   no modifier. Anything else — a new tab, a new window, a download — is
   the browser's, and the page leaves it alone. */
function plainClick(e) {
  return !!e && !e.defaultPrevented && (e.button === undefined || e.button === 0) &&
    !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}

/* how many entries this page has written: for a test, and for a person at
   the console */
function routeWrites() { return writes; }

export {
  holdRoute, linkFor, plainClick, replaceRoute, routeReady, routeWrites,
  setRouteSource, syncRoute
};
