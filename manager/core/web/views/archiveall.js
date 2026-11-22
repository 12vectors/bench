/* Archive all, from the Done column's header (task 187).

   Opening the sheet is a read: it asks the board what Archive all would
   take from done/ (`GET /api/archive/all`) and shows that exact set, with
   every card it would leave and why — a phase card, a member of a phase
   still open (the Board does not draw those), a card an agent works on.
   Nothing is written until the person presses the named button, and the
   set sent is the one shown: a card completed after that is not taken.

   The board then reads each card again and archives it through the single
   archive door, one at a time. The sheet follows the batch (`?request=`)
   and ends on a summary that keeps archived, skipped and failed apart,
   with each card's reason. Partial success stays visible: a retry asks
   for only the outstanding cards, under a new request that names the one
   it retries. A request that got no answer is never assumed to have
   failed — sending it again reuses its identity, so the board answers
   with the batch it already has rather than starting a second. */
import { fetch, post } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { toast } from '../lib/toast.js';

const SHEET = 'archallsheet';
let timer = null;
let closer = null;

function requestId() {
  return `aa-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function cardName(row) {
  return `${row.number ? '#' + row.number + ' ' : ''}${row.title || row.file}`;
}

function sheet() { return document.getElementById(SHEET); }

function head() {
  return `<div class="stitle"><span>Archive all</span>` +
    `<span class="mono">done · tasks/archive/</span></div>`;
}

function paint(html) {
  const el = sheet();
  if (el) el.innerHTML = head() + html;
  return el;
}

function close() {
  clearTimeout(timer);
  if (closer) closer();
}

async function read(url) {
  try {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data,
             error: res.ok ? null : data.error || `the board answered ${res.status}` };
  } catch {
    return { ok: false, status: 0, data: {}, error: 'the board could not be asked' };
  }
}

function onBackdrop(e) {
  if (e.target === e.currentTarget && sheet()) close();
}

/* The entry point: `closeSheet` is the page's own, handed in by main.js
   so this module imports no other view. */
async function openArchiveAll(closeSheet) {
  closer = closeSheet;
  const wrap = $('#sheetwrap');
  wrap.innerHTML = `<div class="sheet" id="${SHEET}" role="dialog" aria-label="Archive all done tasks">` +
    `${head()}<p role="status">Reading what Archive all would take…</p></div>`;
  wrap.classList.add('open');
  // one named listener: adding it again on the next open is a no-op, so
  // backdrop clicks never stack up across openings
  wrap.addEventListener('click', onBackdrop);
  const r = await read('/api/archive/all');
  if (!sheet()) return;
  if (!r.ok) {
    paint(`<p class="bad" role="alert">${esc(r.error)}</p>` + buttons([], 'Nothing was archived.'));
    wireClose();
    return;
  }
  if (r.data.running) { follow(r.data.running, []); return; }
  confirmView(r.data);
}

function buttons(extra, closeNote) {
  return `<div class="sbtns">${extra.join('')}` +
    `<button type="button" id="aa-close">Close<small>${esc(closeNote)}</small></button></div>`;
}

function wireClose() {
  const b = $('#aa-close');
  if (b) b.addEventListener('click', close);
}

function confirmView(reading) {
  const eligible = reading.eligible || [];
  const excluded = reading.excluded || [];
  const n = eligible.length;
  const list = n
    ? `<ul class="aa-list" aria-label="Cards that would be archived">` +
      eligible.map(r => `<li data-file="${esc(r.file)}">${esc(cardName(r))}</li>`).join('') + `</ul>`
    : '';
  const left = excluded.length
    ? `<details class="aa-left"${n ? '' : ' open'}><summary>Left in Done: ${excluded.length} ` +
      `card${excluded.length === 1 ? '' : 's'}, each with why</summary><ul class="aa-list">` +
      excluded.map(r => `<li data-file="${esc(r.file)}">${esc(cardName(r))} — ` +
        `<span class="why">${esc(r.reason)}</span></li>`).join('') + `</ul></details>`
    : '';
  const sync = reading.sync && reading.sync.enabled
    ? ' Each archive is committed and then published by sync; the summary says whether ' +
      'publishing worked, apart from the archive itself.'
    : '';
  const scope = n
    ? `<p><strong>Archives ${n} card${n === 1 ? '' : 's'} from Done</strong> — every eligible card ` +
      `in done/ on this board, including any the Board does not draw, not only the cards ` +
      `on screen. No other stage and no phase card is touched.</p>`
    : `<p><strong>Nothing to archive.</strong> No card in Done is eligible for Archive all` +
      `${excluded.length ? ' — the ones there are listed below with why' : ''}.</p>`;
  paint(scope + list + left +
    (n ? `<p>Cards completed after you confirm are not included. Each card is read again ` +
         `just before it is archived; one that has moved, gone or become protected is ` +
         `skipped and said so. Archiving keeps every card and its history in ` +
         `tasks/archive/ — ⌘Z brings this set back. Nothing is merged, cleaned up or ` +
         `launched.${sync}</p>` : '') +
    buttons(n ? [`<button type="button" class="primary" id="aa-go">Archive ${n} card` +
                 `${n === 1 ? '' : 's'}<small>The ${n} listed above, one at a time.</small></button>`] : [],
            n ? 'Cancel — nothing changes.' : 'Nothing was archived.'));
  wireClose();
  const go = $('#aa-go');
  if (go) {
    const files = eligible.map(r => r.file);
    const request = requestId();
    go.addEventListener('click', () => send(go, request, files, null));
    go.focus();
  }
}

async function send(btn, request, files, retryOf) {
  if (btn) btn.disabled = true;
  const body = { request, files };
  if (retryOf) body.retryOf = retryOf;
  const r = await post('/api/archive/all', body);
  if (!sheet()) return;
  if (r.status === 0) {
    // not a failure: nothing is known. The same identity again is safe.
    paint(`<p class="bad" role="alert">The request got no answer, so nothing is known to ` +
          `have been archived. Sending it again is safe: the board answers with the batch ` +
          `it already has rather than starting a second.</p>` +
          buttons([`<button type="button" class="primary" id="aa-again">Ask again` +
                   `<small>Same request, ${files.length} card${files.length === 1 ? '' : 's'}.</small></button>`],
                  'Nothing more is sent.'));
    wireClose();
    $('#aa-again').addEventListener('click', (e) => send(e.currentTarget, request, files, retryOf));
    return;
  }
  if (!r.ok) {
    paint(`<p class="bad" role="alert">${esc(r.error)}</p>` + buttons([], 'Nothing was archived.'));
    wireClose();
    toast(r.error || 'Archive all was refused', true);
    return;
  }
  follow(request, files, r.data.batch);
}

async function follow(request, files, batch) {
  clearTimeout(timer);
  if (!batch) {
    const r = await read('/api/archive/all?request=' + encodeURIComponent(request));
    if (!sheet()) return;
    if (!r.ok) {
      paint(`<p class="bad" role="alert">${esc(r.error)}</p>` + buttons([], ''));
      wireClose();
      return;
    }
    batch = r.data.batch;
  }
  if (batch.status === 'running') {
    const done = (batch.items || []).length;
    paint(`<p role="status" aria-live="polite" class="aa-progress">Archiving… ${done} of ` +
          `${batch.total} read${batch.current ? ` — now ${esc(batch.current)}` : ''}.</p>` +
          itemsList(batch) +
          buttons([], 'The batch keeps running; its summary is in the activity log.'));
    wireClose();
    timer = setTimeout(() => { if (sheet()) follow(request, files); }, 600);
    return;
  }
  summary(batch);
}

const OUTCOME = {
  archived: 'archived',
  committed: 'archived — now committed',
  'already-archived': 'already archived',
  skipped: 'skipped',
  failed: 'failed',
};

function itemsList(batch) {
  const items = batch.items || [];
  if (!items.length) return '';
  return `<ul class="aa-list aa-items">` + items.map(i => {
    const cls = i.outcome === 'failed' || i.committed === false ? 'bad'
      : i.outcome === 'skipped' ? 'skip' : 'ok';
    const word = i.committed === false ? 'archived — not committed' : OUTCOME[i.outcome] || i.outcome;
    return `<li class="${cls}" data-file="${esc(i.file)}" data-outcome="${esc(i.outcome)}">` +
      `<span class="mono">${esc(i.file)}</span> — ${esc(word)}` +
      (i.reason ? `<span class="why"> · ${esc(i.reason)}</span>` : '') + `</li>`;
  }).join('') + `</ul>`;
}

function syncLine(batch) {
  const s = batch.sync;
  if (!s || !s.enabled) {
    const committed = (batch.commits || 0) > 0 ||
      (batch.items || []).some(i => i.committed === true);
    if (committed) return 'Sync is off: the archive is committed on this board only.';
    if (batch.archived) {
      return 'Sync is off, and board commits are off: the cards are moved on this ' +
        'board and nothing was committed — commit tasks/archive/ yourself.';
    }
    return 'Sync is off: nothing was published.';
  }
  const st = s.status || {};
  if (st.state === 'ok') return 'Sync: published — the other boards will see it.';
  return `Sync: not published yet (${st.state || s.outcome || 'unknown'})` +
    `${st.detail ? ' — ' + st.detail : ''}. The cards are archived on this board either way.`;
}

function summary(batch) {
  const parts = [`${batch.archived} archived`];
  if (batch.already) parts.push(`${batch.already} already archived`);
  if (batch.skipped) parts.push(`${batch.skipped} skipped`);
  if (batch.failed) parts.push(`${batch.failed} failed`);
  if (batch.uncommitted) parts.push(`${batch.uncommitted} not committed`);
  const out = batch.outstanding || [];
  const interrupted = batch.status === 'interrupted'
    ? `<p class="bad" role="alert">This batch was interrupted before it finished — the cards ` +
      `it did not reach are outstanding.</p>` : '';
  const partial = out.length
    ? `<p class="bad">${out.length} card${out.length === 1 ? ' is' : 's are'} outstanding: ` +
      `this was not all-or-nothing — the cards archived stay archived. A retry asks for ` +
      `only the outstanding ones.</p>` : '';
  paint(`<p role="status" class="aa-summary"><strong>${esc(parts.join(', '))}</strong> ` +
        `of ${batch.total}.</p>` + interrupted + partial + itemsList(batch) +
        `<p class="why aa-sync">${esc(syncLine(batch))}</p>` +
        (batch.archived ? `<p class="why">⌘Z brings this set back to Done.</p>` : '') +
        buttons(out.length ? [`<button type="button" class="primary" id="aa-retry">` +
                              `Retry ${out.length} outstanding<small>Only these; nothing else is sent.</small></button>`] : [],
                'The summary is also in the activity log.'));
  wireClose();
  const retry = $('#aa-retry');
  if (retry) {
    const request = requestId();
    retry.addEventListener('click', () => send(retry, request, out, batch.request));
  }
  toast(`Archive all: ${parts.join(', ')}`, !!(batch.failed || batch.uncommitted
                                                || batch.status === 'interrupted'));
}

export {
  openArchiveAll
};
