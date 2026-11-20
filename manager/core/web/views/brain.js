/* The Brain (task 157): the list of maintained entries in the work root's
   brain/, and one entry at its stable address `/brain/e/<id>`.

   Everything drawn here is the server's reading (`GET /api/brain`,
   `GET /api/brain/entry`), which reads brain/ and nothing else. Opening
   the view, an entry or a link writes nothing and launches nothing. An
   entry's text is untrusted: it goes through the page's own Markdown
   renderer, which escapes before it writes a tag, and every other string
   through `esc`. Writing entries on the board is #158's; this view says so
   rather than offering a control that does nothing. */
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { md } from '../lib/md.js';

const CLASSES = ['Current', 'Historical', 'Superseded', 'Unclassified'];
const ABOUT = {
  Current: 'operative knowledge, accepted work not yet done, or an explanation current evidence supports',
  Historical: 'a valid record that no longer directs work',
  Superseded: 'its claims were explicitly replaced or invalidated',
  Unclassified: 'not yet assessed, or the evidence is missing or conflicts',
};
// a reading is asked again at most this often while the view repaints
const FRESH_MS = 4000;

const cache = { index: null, entries: {} };   // key -> {data, status, at, error}
const inflight = new Set();
let painted = '';
let paintedKey = null;
let returnTo = null;

function key() { return S.brainEntry ? 'e:' + S.brainEntry : 'index'; }

async function load(k) {
  if (inflight.has(k)) return;
  inflight.add(k);
  const url = k === 'index' ? '/api/brain'
    : '/api/brain/entry?id=' + encodeURIComponent(k.slice(2));
  let slot;
  try {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    slot = { status: res.status, data: k === 'index' ? data.brain : data.entry,
             error: data.error || null, at: Date.now() };
  } catch (e) {
    slot = { status: 0, data: null, error: 'the board could not be asked', at: Date.now() };
  }
  inflight.delete(k);
  // a failed re-read keeps what was last read, marked
  const old = k === 'index' ? cache.index : cache.entries[k];
  if (!slot.data && old && old.data) slot = { ...old, error: slot.error, at: slot.at };
  if (k === 'index') cache.index = slot; else cache.entries[k] = slot;
  if (S.view === 'brain' && key() === k) renderBrain();
}

const chip = (cls, text, title) =>
  `<span class="b-chip ${cls}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
const classChip = (c) => c ? chip('b-c-' + c.toLowerCase(), c, ABOUT[c]) : '';
const entryHref = (id) => '/brain/e/' + encodeURIComponent(id);
const entryLink = (e) => `<a href="${esc(entryHref(e.id))}" data-route data-brain-id="${esc(e.id)}">` +
  `${esc(e.title || e.id)}</a>`;

/* What fills the Brain, said the same way wherever it is empty: what is
   true now, and the one action available today. */
function emptyGuidance(dir) {
  const where = dir && dir.state === 'missing'
    ? '<p>There is no <code>brain/</code> directory in this board\'s work root yet. Reading never creates it.</p>'
    : '<p><code>brain/</code> holds no entries yet.</p>';
  return `<div class="b-panel b-empty">${where}` +
    '<p>An entry is one Markdown file in <code>brain/</code> under the work root, committed to the ' +
    'work repository like the cards are. Today entries are written by hand, following the ' +
    'version 1 format in AGENTS.md (“The Brain: maintained entries, read”); a file appears here ' +
    'as soon as it is saved.</p>' +
    '<p class="b-note">Writing and classifying entries on the board comes with card #158. Nothing ' +
    'here imports reviews or reference/ files.</p></div>';
}

function diagnosticsBlock(index) {
  const list = index.diagnostics || [];
  if (!list.length) return '';
  const more = index.diagnosticsOmitted ? `<li>${index.diagnosticsOmitted} more not shown.</li>` : '';
  return `<details class="b-panel b-diag" open><summary>${list.length + (index.diagnosticsOmitted || 0)} ` +
    `file${list.length === 1 ? '' : 's'} in brain/ could not be shown</summary><ul>` +
    list.map(d => `<li><code>${esc(d.file)}</code> — <strong>${esc(d.code)}</strong>: ${esc(d.message)}</li>`).join('') +
    more + '</ul><p class="b-note">Nothing was merged or guessed: fix the file and it appears on the next ' +
    'reading.</p></details>';
}

function listHtml(slot) {
  const index = slot.data;
  const head = '<header class="b-head"><h1 class="b-title" tabindex="-1">Brain</h1>' +
    '<p class="b-lede">Maintained project knowledge kept in the work repository\'s ' +
    '<code>brain/</code>. Each entry has a stable address that survives a rename, a new title ' +
    'or a new classification. An entry explains; it does not override configuration, ' +
    'merge rules or a person\'s decision.</p></header>';
  const stale = slot.error ? `<p class="b-warn" role="status">${esc(slot.error)} — showing the last reading.</p>` : '';
  if (index.directory.state === 'refused') {
    return head + stale + `<div class="b-panel b-bad" role="alert"><p><strong>brain/ was not read.</strong> ` +
      `${esc(index.directory.why)}.</p></div>`;
  }
  if (!index.entries.length) return head + stale + emptyGuidance(index.directory) + diagnosticsBlock(index);
  const sections = CLASSES.map(c => {
    const rows = index.entries.filter(e => e.classification === c);
    const body = rows.length ? '<ul class="b-list">' + rows.map(e =>
      `<li class="b-row${e.tombstone ? ' b-tomb' : ''}"><div class="b-rowhead">${entryLink(e)}` +
      chip('b-kind', e.kind) + (e.tombstone ? chip('b-c-removed', 'Removed') : '') + '</div>' +
      (e.summary ? `<p class="b-sum">${esc(e.summary)}</p>` : '<p class="b-sum b-none">No summary.</p>') +
      `<p class="b-meta mono">${esc(e.id)}${e.sources.length ? ' · from ' +
        e.sources.slice(0, 3).map(s => esc(s.text)).join(', ') : ''}</p></li>`).join('') + '</ul>'
      : '<p class="b-none">None.</p>';
    return `<section class="b-panel" aria-labelledby="bsec-${c}"><h2 id="bsec-${c}">${c} ` +
      `<span class="b-count">${rows.length}</span></h2><p class="b-note">${esc(ABOUT[c])}.</p>${body}</section>`;
  }).join('');
  return head + stale + sections + diagnosticsBlock(index) +
    `<p class="b-note mono">brain/ revision ${esc(index.revision)} · ${index.entries.length} ` +
    `entr${index.entries.length === 1 ? 'y' : 'ies'}</p>`;
}

function linkList(label, items) {
  if (!items || !items.length) return '';
  return `<dt>${label}</dt><dd><ul class="b-links">` + items.map(t => t.present
    ? `<li>${entryLink(t)} <span class="b-meta mono">${esc(t.id)}</span></li>`
    : `<li><span class="mono">${esc(t.id)}</span> <span class="b-missing">not an entry on this board</span></li>`
  ).join('') + '</ul></dd>';
}

function sourceItem(s) {
  if (s.kind === 'card') {
    return s.present
      ? `<li><a href="${esc(s.address)}" data-route>Card #${esc(s.card)}</a></li>`
      : `<li>Card #${esc(s.card)} <span class="b-missing">not on this board</span></li>`;
  }
  if (s.kind === 'file') {
    return s.present
      ? `<li><a href="${esc(s.address)}" target="_blank" rel="noopener noreferrer">${esc(s.text)}</a></li>`
      : `<li><span class="mono">${esc(s.text)}</span> <span class="b-missing">not in this work root</span></li>`;
  }
  if (s.kind === 'web') {
    return `<li><a href="${esc(s.address)}" target="_blank" rel="noopener noreferrer">${esc(s.text)}</a> ` +
      '<span class="b-note">(not fetched or checked)</span></li>';
  }
  return `<li>${esc(s.text)} <span class="b-note">(as written; not a link bench follows)</span></li>`;
}

const back = '<p class="b-back"><a href="/brain" data-route>All Brain entries</a></p>';

function entryHtml(slot, id) {
  const e = slot.data;
  if (!e) {
    return back + `<div class="b-panel b-bad" role="alert"><h1 class="b-title" tabindex="-1">Entry ` +
      `${esc(id)}</h1><p>${esc(slot.error || 'the entry could not be read')}</p></div>`;
  }
  if (e.state === 'unknown') {
    return back + '<div class="b-panel"><h1 class="b-title" tabindex="-1">No entry ' + esc(id) +
      ' on this board</h1><p>No file in <code>brain/</code> carries this id, and none ever kept it as ' +
      'removed. It may belong to another board, or it was never written.</p></div>';
  }
  if (e.state === 'duplicate') {
    return back + `<div class="b-panel b-bad" role="alert"><h1 class="b-title" tabindex="-1">${esc(id)} ` +
      `is claimed by ${e.files.length} files</h1><p>None of them is shown under this address until ` +
      'only one carries the id:</p><ul>' + e.files.map(f => `<li><code>brain/${esc(f)}</code></li>`).join('') +
      '</ul></div>';
  }
  if (e.state === 'invalid' || e.state === 'unsupported') {
    return back + `<div class="b-panel b-bad" role="alert"><h1 class="b-title" tabindex="-1">${esc(id)} ` +
      `cannot be shown</h1><p><code>brain/${esc(e.file)}</code> ${e.state === 'unsupported'
        ? 'uses a Brain contract version this board does not read' : 'does not follow the entry format'}:</p>` +
      '<ul>' + e.problems.map(p => `<li>${esc(p)}</li>`).join('') + '</ul></div>';
  }
  const tomb = e.state === 'tombstone';
  const stale = slot.error ? `<p class="b-warn" role="status">${esc(slot.error)} — showing the last reading.</p>` : '';
  const removed = tomb ? `<div class="b-panel b-tombnote" role="note"><strong>This entry was removed.</strong> ` +
    `${esc(e.removed)}` + (e.replacedBy.length ? ` Replaced by ${e.replacedBy.map(t => t.present
      ? entryLink(t) : esc(t.id)).join(', ')}.` : '') +
    ' Its address is kept so links to it still say what happened.</div>' : '';
  const superseded = !tomb && e.supersededBy.length
    ? `<p class="b-warn">Superseded by ${e.supersededBy.map(t => t.present ? entryLink(t) : esc(t.id)).join(', ')}.</p>` : '';
  const facts = '<dl class="b-facts">' +
    `<dt>Id</dt><dd class="mono">${esc(e.id)}</dd>` +
    `<dt>Kind</dt><dd>${esc(e.entryKind)}</dd>` +
    `<dt>Classification</dt><dd>${classChip(e.classification)} <span class="b-note">${esc(ABOUT[e.classification] || '')}</span></dd>` +
    `<dt>Sources</dt><dd>${e.sources.length ? '<ul class="b-links">' + e.sources.map(sourceItem).join('') + '</ul>'
      : '<span class="b-none">No source is recorded.</span>'}</dd>` +
    linkList('Related', e.related) + linkList('Supersedes', e.supersedes) +
    linkList('Superseded by', e.supersededBy) + linkList('Linked from', e.linkedFrom.map(x => ({ ...x, present: true }))) +
    `<dt>File</dt><dd class="mono">brain/${esc(e.file)}</dd>` +
    `<dt>Revision</dt><dd class="mono" title="a digest of the file's bytes as read">${esc(e.revision)}</dd>` +
    '</dl>';
  const history = e.history.length
    ? '<ol class="b-history">' + e.history.slice().reverse().map(h =>
        `<li><span class="mono">${esc(h.at)}</span> ${classChip(h.classification)} ` +
        `<span>${esc(h.actor)}</span> — ${esc(h.reason)}</li>`).join('') + '</ol>'
    : '<p class="b-none">No classification reason is recorded.</p>';
  const warnings = e.warnings.length
    ? `<details class="b-panel b-diag"><summary>${e.warnings.length} note${e.warnings.length === 1 ? '' : 's'} ` +
      'about this entry\'s format</summary><ul>' + e.warnings.map(w => `<li>${esc(w)}</li>`).join('') + '</ul></details>'
    : '';
  return back + stale +
    `<article class="b-entry" aria-labelledby="btitle"><header class="b-head">` +
    `<p class="b-kicker">${chip('b-kind', e.entryKind)} ${classChip(e.classification)}` +
    `${tomb ? chip('b-c-removed', 'Removed') : ''}</p>` +
    `<h1 class="b-title" id="btitle" tabindex="-1">${esc(e.title)}</h1>` +
    (e.summary ? `<p class="b-lede">${esc(e.summary)}</p>` : '') +
    `<p class="b-permalink"><span class="mono">${esc(entryHref(e.id))}</span> ` +
    `<button type="button" data-copy-link="${esc(entryHref(e.id))}">Copy link</button></p></header>` +
    removed + superseded +
    `<div class="b-read"><div class="b-prose">${md(e.body, { base: 2 }) ||
      '<p class="b-none">This entry has no body.</p>'}</div>` +
    `<aside class="b-side" aria-label="About this entry"><div class="b-panel">${facts}</div>` +
    `<div class="b-panel"><h2>Classification history</h2>${history}</div></aside></div>` +
    warnings + '</article>';
}

/* A link written in an entry is resolved against the entry's own
   address, so `[the other one](e-other)` reaches `/brain/e/e-other`. One
   to the board's own pages is followed in this tab like any other link
   here; anything else opens in a new one. md() already refused every
   scheme but the web, mail and anchors. */
function settleLinks(el, id) {
  const base = new URL(entryHref(id), location.origin);
  el.querySelectorAll('.b-prose a[href]').forEach(a => {
    const raw = a.getAttribute('href');
    if (raw.startsWith('#') || /^mailto:/i.test(raw)) return;
    let url;
    try { url = new URL(raw, base); } catch (err) { return; }
    if (url.origin === location.origin) {
      a.setAttribute('href', url.pathname + url.search + url.hash);
      if (/^\/(brain|cards|board|phases|settings|orchestrator|sessions|focus)(\/|$)/.test(url.pathname)) {
        a.setAttribute('data-route', '');
        return;
      }
    }
    a.setAttribute('target', '_blank');
    a.setAttribute('rel', 'noopener noreferrer');
  });
}

function renderBrain() {
  const el = $('#bshell');
  if (!el) return;
  const k = key();
  let html;
  if (S.brainBad) {
    html = back + '<div class="b-panel" role="alert"><h1 class="b-title" tabindex="-1">Not a Brain address</h1>' +
      `<p><code>${esc(S.brainBad)}</code> does not name an entry. An entry's address is ` +
      '<code>/brain/e/&lt;id&gt;</code>, its id in lower-case letters, digits and hyphens.</p></div>';
  } else {
    const slot = k === 'index' ? cache.index : cache.entries[k];
    if (!slot || Date.now() - slot.at > FRESH_MS) load(k);
    html = !slot ? '<p class="b-loading" role="status" aria-busy="true">Reading brain/…</p>'
      : k === 'index' ? (slot.data ? listHtml(slot)
        : `<div class="b-panel b-bad" role="alert"><h1 class="b-title" tabindex="-1">Brain</h1><p>` +
          `${esc(slot.error || 'brain/ could not be read')}</p></div>`)
      : entryHtml(slot, k.slice(2));
  }
  const arrived = k !== paintedKey && !/b-loading/.test(html);
  if (html !== painted) {
    // a live repaint keeps the reader's place: replaced only when the
    // reading changed, and focus is put back where it was
    const active = document.activeElement;
    const keep = active && el.contains(active) && active.dataset ? active.dataset.brainId : null;
    el.innerHTML = `<div class="b-main">${html}</div>`;
    painted = html;
    if (S.brainEntry) settleLinks(el, S.brainEntry);
    if (keep) el.querySelector(`[data-brain-id="${CSS.escape(keep)}"]`)?.focus();
  }
  if (arrived) {
    // a new destination: focus its heading, or, back on the list, the
    // entry the reader came from
    paintedKey = k;
    const target = (k === 'index' && returnTo && el.querySelector(`.b-list [data-brain-id="${CSS.escape(returnTo)}"]`))
      || el.querySelector('.b-title');
    if (target && document.activeElement !== target) target.focus();
    if (k !== 'index') { el.scrollTop = 0; returnTo = k.slice(2); }
  }
}

export {
  renderBrain
};
