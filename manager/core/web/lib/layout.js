/* The phone layout (task 154): one breakpoint, read in one place.

   Below it the page is a phone's: a compact header, one stage per screen
   with a stage strip, and an activity panel that starts closed and is
   sized to leave the board in view. The CSS uses the same width
   (board.css, `max-width:760px`); this is the script's reading of it.
   No DOM writes, no state. */
const NARROW = '(max-width: 760px)';

function isNarrow() {
  return typeof matchMedia === 'function' && matchMedia(NARROW).matches;
}

/* Which remembered activity choice applies: a phone's and a desktop's are
   kept apart, so opening the panel on a wide window never opens it on a
   phone, and a phone starts compact until someone opens it there. */
function logOpenKey(narrow = isNarrow()) {
  return narrow ? 'bench-log-open-narrow' : 'bench-log-open';
}

export {
  isNarrow, logOpenKey, NARROW
};
