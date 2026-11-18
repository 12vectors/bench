/* What a destination an address named says about itself (task 134), in
   one set of words for every view that draws one: Sessions and Focus say
   the same thing about a session, so neither has to import the other. */
import { esc } from './dom.js';
import { sessionIdOf } from './route.js';

/* A session an address named that this board does not have: said, never
   a blank and never another session in its place. */
function sessionUnavailable(sid) {
  const valid = !!sessionIdOf(sid);
  return `<div class="f-empty" role="alert"><b>${valid
      ? `Session ${esc(String(sid).slice(0, 12))} is not on this board.`
      : 'That is not a session id.'}</b> ` +
    (valid ? 'Its history may have been removed, or it was recorded on another board. ' : '') +
    'Nothing else has been selected in its place.</div>';
}

/* A session's status in words: still reporting, or ended — an ended
   session's history is kept, and it stays linkable. `live` is the
   caller's reading of whether the session is still reporting. */
function sessionStatusWord(meta, live) {
  return live && meta.status === 'active' ? 'live'
    : meta.status === 'ended' ? 'ended' : (meta.status || 'idle');
}

export {
  sessionStatusWord, sessionUnavailable
};
