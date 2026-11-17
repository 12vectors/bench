import { S } from '../app/state.js';
import { PAGE_BUILD } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';

/* A page from an earlier build, told so where it cannot be missed.

   The board refuses a mutation from a page whose build is not the one it
   serves (`stale-page`): the board restarted under it, and this page's
   code may not match the rules it now enforces. The refusal changed
   nothing, so the remedy is a reload — and a reload throws away whatever
   this page holds unsent, so the banner says that first and names what it
   can see: a comment, a note, a draft. It is drawn outside every
   demolished root, so a redraw neither removes nor re-announces it. */
function unsavedWork() {
  const found = [];
  const texts = (o) => Object.values(o || {}).some(v => typeof v === 'string' && v.trim());
  if (texts(S.commentDraft)) found.push('a comment you have not sent');
  if (texts(S.accDraft)) found.push('an acceptance or finding note');
  if (S.draft && S.draft.dirty) found.push('an unsaved Settings draft');
  if (S.agentsDraft) found.push('unsaved agent settings');
  for (const el of document.querySelectorAll('textarea, input[type=text]')) {
    if (el.value && el.value.trim() && !found.length) {
      found.push('text typed into this page');
      break;
    }
  }
  return found;
}

function showStalePage(data) {
  const el = $('#stalebanner');
  if (!el) return;
  const served = (data && data.build) || 'a newer build';
  const unsaved = unsavedWork();
  el.innerHTML =
    `<p><b>This page is out of date.</b> The board restarted and now serves build ` +
    `<span class="mono">${esc(served)}</span>; this page is ` +
    `<span class="mono">${esc(PAGE_BUILD || 'unknown')}</span>. Your last change was ` +
    `refused and nothing was changed. Reload to update.</p>` +
    `<p class="stalewarn">${unsaved.length
      ? `Reloading discards ${esc(unsaved.join(', '))}. Copy it somewhere first.`
      : 'Reloading discards anything typed into this page and not yet sent.'}</p>` +
    `<button type="button" id="stalereload">Reload</button>`;
  el.hidden = false;
  $('#stalereload').addEventListener('click', () => location.reload());
}

export {
  showStalePage, unsavedWork
};
