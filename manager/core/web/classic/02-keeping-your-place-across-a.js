'use strict';
/* ── keeping your place across a redraw ───────────────────────────────── */

/* The page redraws by demolition: every pass throws the DOM away and
   builds it again, so a scrolling element it rebuilds is a brand-new node
   and a new node's scrollTop is 0. render() runs on every SSE frame and a
   working agent emits an event per tool use, so without this you lose your
   place several times a minute — precisely when you are trying to read.

   The cure is small and paid separately: note where each scroller was
   before the wipe, put it back once the new nodes are in. Both halves run
   inside the same synchronous render, so nothing flashes.

   Keys are stable names rather than nodes — the stage slug for a column,
   the session id for a timeline — because the node itself does not
   survive. A `v:` prefix marks a key belonging to a view; those are
   dropped when you switch views, so coming back is a fresh look rather
   than a stale position from a previous visit. The activity log spans
   every view, so its key carries no prefix and outlives the switch. */
const scrollMarks = {};

function markScroll(key, el) {
  if (!el) return;
  scrollMarks[key] = { top: el.scrollTop, left: el.scrollLeft };
}

/* Clamp rather than guess. A column whose cards moved on may be shorter
   than it was, and an offset past the new bottom should land at the new
   bottom — not throw, not blank. (Content that changed *above* the
   viewport still shifts what you are looking at. That is honest, and it is
   not what this is for.) */
function restoreScroll(key, el) {
  const at = scrollMarks[key];
  // a scroller that was at the top is already where it belongs, and
  // saying so here is what keeps the common frame free of the layout
  // flush that reading scrollHeight costs
  if (!el || !at || (!at.top && !at.left)) return;
  el.scrollTop = Math.max(0, Math.min(at.top, el.scrollHeight - el.clientHeight));
  el.scrollLeft = Math.max(0, Math.min(at.left, el.scrollWidth - el.clientWidth));
}

function forgetViewScroll() {
  for (const key of Object.keys(scrollMarks)) {
    if (key.startsWith('v:')) delete scrollMarks[key];
  }
}

