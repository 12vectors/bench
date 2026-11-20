/* The Brain (tasks 157, 158): the list of maintained entries in the work
   root's brain/, one entry at its stable address `/brain/e/<id>`, and the
   person's writes to them — create, edit, classify, the manual override
   and retirement.

   Everything drawn here is the server's reading (`GET /api/brain`,
   `GET /api/brain/entry`, `GET /api/brain/revisions`,
   `GET /api/brain/operation`), which reads and writes nothing. A write is
   one explicit Save (or an armed Retire) through `POST /api/brain/<kind>`,
   bound to the revision this page read and to a request identity; the
   server refuses a stale one and says what is on disk now, and the draft
   stays here to bring across. Editing is a mode of the entry's page, never
   an address: following a link or going Back reads; it never writes.

   An entry's text is untrusted: it goes through the page's own Markdown
   renderer, which escapes before it writes a tag, and every other string
   through `esc`. */
import { S } from '../app/state.js';
import { fetch, post } from '../lib/api.js';
import { arming, phaseOf } from '../lib/arm.js';
import { $, esc } from '../lib/dom.js';
import { md } from '../lib/md.js';
import { syncRoute } from '../lib/nav.js';
import { pressHold } from '../lib/press.js';
import { toast } from '../lib/toast.js';

const CLASSES = ['Current', 'Historical', 'Superseded', 'Unclassified'];
const KINDS = ['Guide', 'Investigation', 'Decision', 'Review', 'Prototype', 'Attachment'];
const ABOUT = {
  Current: 'operative knowledge, accepted work not yet done, or an explanation current evidence supports',
  Historical: 'a valid record that no longer directs work',
  Superseded: 'its claims were explicitly replaced or invalidated',
  Unclassified: 'not yet assessed, or the evidence is missing or conflicts',
};
// a reading is asked again at most this often while the view repaints
const FRESH_MS = 4000;

const cache = { index: null, entries: {}, revisions: {} };   // key -> {data, status, at, error}
const inflight = new Set();
let painted = '';
let paintedKey = null;
let returnTo = null;

/* The one write in progress on this page, or null. `mode` is create,
   edit, classify, override or retire; `draft` is what the person typed,
   kept across every repaint and every refusal; `base` the revision it
   was started from; `request` the identity a Save sends — the same one
   again after no answer, a new one after any answer. */
let ed = null;
// the newest write this page made per entry, read again while it settles
const ops = {};
const armed = arming();
let wired = false;
let press = null;
let revsOpen = null;     // the entry whose Revisions fold is open

function key() { return S.brainEntry ? 'e:' + S.brainEntry : 'index'; }
function newRequest() {
  return 'bw-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

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

async function loadRevisions(id) {
  const k = 'r:' + id;
  if (inflight.has(k)) return;
  inflight.add(k);
  let slot;
  try {
    const res = await fetch('/api/brain/revisions?id=' + encodeURIComponent(id));
    const data = await res.json().catch(() => ({}));
    slot = { data: data.revisions || null, error: data.error || null, at: Date.now() };
  } catch (e) {
    slot = { data: null, error: 'the board could not be asked', at: Date.now() };
  }
  inflight.delete(k);
  cache.revisions[id] = slot;
  renderBrain();
}

async function loadOp(id) {
  const o = ops[id];
  if (!o || inflight.has('o:' + id)) return;
  inflight.add('o:' + id);
  try {
    const res = await fetch('/api/brain/operation?request=' + encodeURIComponent(o.request));
    const data = await res.json().catch(() => ({}));
    if (data.operation) o.op = data.operation;
  } catch (e) { /* kept as last read */ }
  o.at = Date.now();
  inflight.delete('o:' + id);
  renderBrain();
}

/* A write changed the files: every reading is asked again. What was read
   stays drawn until the new reading lands, rather than blanking the page
   into "Reading…" under the person who just saved. */
function forget() {
  if (cache.index) cache.index.at = 0;
  Object.values(cache.entries).forEach(slot => { slot.at = 0; });
  cache.revisions = {};
}

const chip = (cls, text, title) =>
  `<span class="b-chip ${cls}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
const classChip = (c) => c ? chip('b-c-' + c.toLowerCase(), c, ABOUT[c]) : '';
const entryHref = (id) => '/brain/e/' + encodeURIComponent(id);
const entryLink = (e) => `<a href="${esc(entryHref(e.id))}" data-route data-brain-id="${esc(e.id)}">` +
  `${esc(e.title || e.id)}</a>`;
const button = (act, label, opts = {}) =>
  `<button type="button" class="b-btn${opts.primary ? ' b-primary' : ''}${opts.alarm ? ' b-alarm' : ''}` +
  `${opts.armed ? ' armed' : ''}${opts.busy ? ' busy' : ''}" ` +
  `data-bact="${esc(act)}"${opts.arg ? ` data-barg="${esc(opts.arg)}"` : ''}` +
  `${opts.disabled ? ' disabled' : ''}${opts.busy ? ' aria-busy="true"' : ''}>${esc(label)}</button>`;

/* What fills the Brain, said the same way wherever it is empty: what is
   true now, and the one action available. */
function emptyGuidance(dir) {
  const where = dir && dir.state === 'missing'
    ? '<p>There is no <code>brain/</code> directory in this board\'s work root yet. Reading never ' +
      'creates it; the first entry you save does.</p>'
    : '<p><code>brain/</code> holds no entries yet.</p>';
  return `<div class="b-panel b-empty">${where}` +
    '<p>An entry is one Markdown file in <code>brain/</code> under the work root, committed to the ' +
    'work repository like the cards are. Write one with <strong>New entry</strong>, or by hand ' +
    'following the version 1 format in AGENTS.md (“The Brain”); a file appears here as soon as it ' +
    'is saved.</p><p class="b-note">Nothing here imports reviews or reference/ files.</p></div>';
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
  const head = '<header class="b-head"><div class="b-headrow"><h1 class="b-title" tabindex="-1">Brain</h1>' +
    (index.directory.state !== 'refused' ? button('new', 'New entry', { primary: true }) : '') + '</div>' +
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
      chip('b-kind', e.kind) + (e.tombstone ? chip('b-c-removed', 'Removed') : '') +
      (e.override ? chip('b-override', 'Manual override', 'a person fixed this classification') : '') +
      '</div>' +
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

/* ── writing ───────────────────────────────────────────────────────── */

function draftOf(mode, e) {
  if (mode === 'create') {
    return { title: '', kind: 'Guide', summary: '', sources: '', related: '', body: '',
             classification: 'Unclassified', reason: '' };
  }
  if (mode === 'edit') {
    return { title: e.title || '', kind: e.entryKind || 'Guide', summary: e.summary || '',
             sources: (e.sources || []).map(s => s.text).join('\n'),
             related: (e.related || []).map(r => r.id).join(', '), body: e.body || '' };
  }
  if (mode === 'classify') {
    return { classification: e.classification, reason: '',
             supersededBy: (e.supersededBy[0] || {}).id || '', invalidate: false, invalidation: '' };
  }
  if (mode === 'override') return { action: e.override ? 'clear' : 'set', reason: '' };
  return { reason: '', replacedBy: '' };   // retire
}

function startWrite(mode, e) {
  ed = { mode, id: e ? e.id : null, base: e ? e.revision : null, draft: draftOf(mode, e),
         request: newRequest(), preview: false, error: null, conflict: null, busy: false,
         lostReply: false, title: e ? e.title : null };
  armed.disarm('retire');
}

const field = (name, label, html, help) =>
  `<div class="b-field"><label for="bf-${name}">${label}</label>${html}` +
  (help ? `<p class="b-note" id="bh-${name}">${help}</p>` : '') + '</div>';
const input = (name, value, opts = {}) =>
  `<input id="bf-${name}" data-bf="${name}" type="text" value="${esc(value)}"` +
  `${opts.help ? ` aria-describedby="bh-${name}"` : ''}${opts.required ? ' required' : ''}` +
  ` autocomplete="off" spellcheck="${opts.spell ? 'true' : 'false'}">`;
const textarea = (name, value, rows, help) =>
  `<textarea id="bf-${name}" data-bf="${name}" rows="${rows}"${help ? ` aria-describedby="bh-${name}"` : ''}>` +
  `${esc(value)}</textarea>`;
const select = (name, value, options) =>
  `<select id="bf-${name}" data-bf="${name}">` + options.map(o =>
    `<option value="${esc(o)}"${o === value ? ' selected' : ''}>${esc(o)}</option>`).join('') + '</select>';

function entryOptions(exceptId, value) {
  const idx = cache.index && cache.index.data;
  const items = (idx ? idx.entries : []).filter(x => x.id !== exceptId && !x.tombstone);
  return `<option value="">— choose an entry —</option>` + items.map(x =>
    `<option value="${esc(x.id)}"${x.id === value ? ' selected' : ''}>${esc(x.title || x.id)} (${esc(x.id)})</option>`).join('');
}

function classifyFields(d, selfId) {
  const radios = CLASSES.map(c =>
    `<label class="b-radio"><input type="radio" name="bf-classification" data-bf="classification" value="${c}"` +
    `${d.classification === c ? ' checked' : ''}> ${classChip(c)} <span class="b-note">${esc(ABOUT[c])}</span></label>`).join('');
  let sup = '';
  if (d.classification === 'Superseded') {
    sup = '<div class="b-sub"><p class="b-note">Superseded needs the entry that replaces this one, or an ' +
      'explicit decision that its claims are invalid with no replacement.</p>' +
      `<label class="b-radio"><input type="checkbox" data-bf="invalidate"${d.invalidate ? ' checked' : ''}> ` +
      'Invalidated, with no replacing entry</label>' +
      (d.invalidate
        ? field('invalidation', 'Why its claims no longer hold', input('invalidation', d.invalidation))
        : field('supersededBy', 'Replaced by', `<select id="bf-supersededBy" data-bf="supersededBy">` +
          entryOptions(selfId, d.supersededBy) + '</select>')) + '</div>';
  }
  return `<fieldset class="b-fieldset"><legend>Classification</legend>${radios}</fieldset>` +
    '<p class="b-note"><strong>Current</strong> may mean an accepted investigation that is not built ' +
    'yet — it is not a claim that the work shipped.</p>' + sup;
}

function conflictHtml() {
  const c = ed.conflict;
  if (!c) return '';
  const cur = c.current || {};
  const mine = ed.mode === 'edit'
    ? `<pre class="b-diffpane">${esc(ed.draft.title)}\n\n${esc(ed.draft.body)}</pre>`
    : `<pre class="b-diffpane">${esc(JSON.stringify(ed.draft, null, 1))}</pre>`;
  return '<div class="b-panel b-bad b-conflict" role="alert"><h2>Not saved: the entry changed</h2>' +
    `<p>${esc(c.error)}</p><p>Your draft is kept below. Read the current version, bring your change ` +
    'across, then save again. Nothing is retried for you.</p>' +
    '<div class="b-compare"><div><h3>What you tried to save</h3>' + mine + '</div>' +
    `<div><h3>On disk now <span class="mono b-note">${esc(cur.revision || 'unknown')}</span></h3>` +
    (cur.state === 'entry' || cur.state === 'tombstone'
      ? `<pre class="b-diffpane">${esc(cur.title || '')}\n${esc(cur.classification || '')}${cur.state === 'tombstone' ? ' · removed' : ''}\n\n${esc(cur.body || '')}</pre>`
      : `<p>${esc(cur.state || 'it could not be read')}</p>`) + '</div></div>' +
    `<div class="b-actions">${button('reread', 'Use the current version and keep my draft', { primary: true })}` +
    `${button('cancel', 'Discard my draft')}</div></div>`;
}

function formHtml(e) {
  const d = ed.draft;
  const labels = { create: 'New Brain entry', edit: 'Edit entry', classify: 'Classify entry',
                   override: 'Manual override', retire: 'Retire entry' };
  let body = '';
  if (ed.mode === 'create' || ed.mode === 'edit') {
    const tabs = '<div class="b-tabs" role="group" aria-label="Body: write or preview">' +
      `<button type="button" class="b-tab" data-bact="write" aria-pressed="${!ed.preview}">Write</button>` +
      `<button type="button" class="b-tab" data-bact="preview" aria-pressed="${ed.preview}">Preview</button></div>`;
    const bodyField = ed.preview
      ? `<div class="b-panel b-prose b-preview" aria-label="Preview">${md(d.body || '', { base: 2 }) ||
          '<p class="b-none">Nothing to preview.</p>'}</div>`
      : textarea('body', d.body, 14, 'Markdown. Shown through the same safe renderer as every entry.');
    body = field('title', 'Title', input('title', d.title, { required: true, spell: true })) +
      field('kind', 'Kind', select('kind', d.kind, KINDS)) +
      field('summary', 'Summary', input('summary', d.summary, { spell: true }), 'One line for the list. Optional.') +
      field('sources', 'Sources', textarea('sources', d.sources, 3,
        'One per line: a card (#148), plans/… or reference/…, a web address (never fetched), or plain text.'),
        'One per line: a card (#148), plans/… or reference/…, a web address (never fetched), or plain text.') +
      field('related', 'Related entries', input('related', d.related),
        'Entry ids, separated by commas. Each must be an entry on this board.') +
      `<div class="b-field"><span class="b-label">Body</span>${tabs}${bodyField}</div>`;
    if (ed.mode === 'create') {
      body += classifyFields(d, null).replace(/<input type="radio" name="bf-classification" data-bf="classification" value="Superseded"[^>]*>/,
        '<input type="radio" name="bf-classification" data-bf="classification" value="Superseded" disabled>') +
        field('reason', 'Why this classification', input('reason', d.reason),
          'Required unless it stays Unclassified. Kept with your name in its history.');
    }
  } else if (ed.mode === 'classify') {
    body = `<p>Now ${classChip(e.classification)}.${e.override ? ' A manual override is recorded.' : ''}</p>` +
      classifyFields(d, e.id) +
      field('reason', 'Reason', input('reason', d.reason, { required: true }),
        'Required. Recorded with your name and the time in the classification history; a ' +
        'correction is a new line, the earlier ones stay.');
  } else if (ed.mode === 'override') {
    body = `<p class="b-note">A manual override records that a person decided this entry's classification. ` +
      'Later automation must respect it until a person clears it. It changes no other authority.</p>' +
      (e.override ? `<p>In force: <span class="mono">${esc(e.override)}</span></p>` : '<p>No override is recorded.</p>') +
      field('reason', e.override ? 'Why clear it' : 'Why set it', input('reason', d.reason, { required: true }));
  } else {
    const ph = phaseOf(armed.store, 'retire');
    body = '<p>Retiring keeps the file as a tombstone: its address keeps working and says it was ' +
      'removed, and why. Nothing is deleted, and the id is never given to another entry.</p>' +
      field('reason', 'Why it is retired', input('reason', d.reason, { required: true })) +
      field('replacedBy', 'Replaced by (optional)', `<select id="bf-replacedBy" data-bf="replacedBy">` +
        entryOptions(e.id, d.replacedBy) + '</select>');
    return `<section class="b-panel b-form" aria-labelledby="bform-h"><h2 id="bform-h">${labels.retire}</h2>` +
      conflictHtml() + body + errorHtml() +
      '<div class="b-actions">' + button('save', ph === 'armed' ? 'Confirm: retire this entry'
        : ed.busy ? 'Retiring…' : 'Retire', { alarm: true, armed: ph === 'armed', busy: ed.busy,
        disabled: ed.busy }) +
      button('cancel', 'Cancel', { disabled: ed.busy }) +
      (ph === 'armed' ? '<span class="b-note" role="status">Click again to retire. It is kept as a tombstone.</span>' : '') +
      '</div></section>';
  }
  return `<section class="b-panel b-form" aria-labelledby="bform-h"><h2 id="bform-h">${labels[ed.mode]}</h2>` +
    (ed.mode !== 'create' && e && e.revision !== ed.base && !ed.conflict
      ? '<p class="b-warn" role="status">The entry changed on disk since you started. Saving will be ' +
        'refused; use the current version and keep your draft.' + button('reread', 'Use current version') + '</p>' : '') +
    conflictHtml() + body + errorHtml() +
    '<div class="b-actions">' + button('save', ed.busy ? 'Saving…' : 'Save', { primary: true, busy: ed.busy, disabled: ed.busy }) +
    button('cancel', 'Cancel', { disabled: ed.busy }) + '</div></section>';
}

function errorHtml() {
  if (ed.lostReply) {
    return '<p class="b-warn" role="alert">No answer came back, and nothing is known to have been saved. ' +
      'Save again sends the same request, which the board answers from its record: it cannot write twice.</p>';
  }
  return ed.error ? `<p class="b-warn" role="alert">Not saved: ${esc(ed.error)}</p>` : '';
}

/* The state of the newest write this page made to an entry, each fact
   read again from the board: saved, committed, shared. */
function writeStatus(id) {
  const o = ops[id];
  if (!o || !o.op) return '';
  const op = o.op;
  const c = op.commit || {};
  const p = op.push || {};
  const rows = [];
  rows.push(`<li><strong>Saved</strong> to <code>brain/${esc(op.file)}</code> <span class="mono b-note">${esc(op.revision || '')}</span>` +
    (op.onDisk && op.onDisk !== op.revision ? ' <span class="b-note">— the file has changed since</span>' : '') + '</li>');
  if (c.state === 'committed') rows.push(`<li><strong>Committed</strong> <span class="mono">${esc((c.sha || '').slice(0, 10))}</span></li>`);
  else if (c.state === 'off') rows.push(`<li><strong>Not committed</strong>: ${esc(c.detail || '')}</li>`);
  else if (c.state === 'failed') {
    rows.push(`<li class="b-bad-row"><strong>Commit failed</strong>: ${esc(c.detail || '')}. The saved file is kept; ` +
      `nothing was rolled back. ${button('retry-commit', 'Retry commit', { arg: o.request })}</li>`);
  }
  if (p.state === 'shared') rows.push(`<li><strong>Shared</strong>: ${esc(p.upstream || 'the remote')} holds it</li>`);
  else if (p.state === 'pending') rows.push(`<li><strong>Push pending</strong> to ${esc(p.upstream || 'the remote')} — not shared yet ${button('retry-push', 'Push now', { arg: o.request })}</li>`);
  else if (p.state === 'failed') {
    rows.push(`<li class="b-bad-row"><strong>Push failed</strong>: ${esc(p.detail || '')}. Committed here, not shared. ` +
      `${button('retry-push', 'Retry push', { arg: o.request })}</li>`);
  } else if (p.state === 'off') rows.push(`<li><strong>Not shared</strong>: ${esc(p.detail || '')}</li>`);
  return `<div class="b-panel b-status" role="status" aria-live="polite"><h2>Your last change: ${esc(op.what || '')}</h2>` +
    `<ul class="b-steps">${rows.join('')}</ul></div>`;
}

function revisionsHtml(id) {
  const slot = cache.revisions[id];
  let inner;
  if (!slot) inner = '<p class="b-note" role="status">Reading revisions…</p>';
  else if (!slot.data) inner = `<p class="b-warn">${esc(slot.error || 'could not be read')}</p>`;
  else {
    const r = slot.data;
    inner = (r.uncommitted ? '<p class="b-warn">The file has changes that are not committed.</p>' : '') +
      (r.commits.length ? '<ol class="b-history">' + r.commits.map(c =>
        `<li><span class="mono">${esc(c.sha.slice(0, 10))}</span> <span class="mono b-note">${esc(c.at)}</span> ` +
        `${esc(c.author)} — ${esc(c.subject)}</li>`).join('') + '</ol>'
        : '<p class="b-none">No commit of this file in the work repository.</p>') +
      (r.saves.length ? '<h3>Saves from this board</h3><ol class="b-history">' + r.saves.map(s =>
        `<li><span class="mono b-note">${esc(s.at || '')}</span> ${esc(s.what || s.kind)} by ${esc(s.actor || '')} — ` +
        `${s.saved ? esc((s.commit || {}).state === 'committed' ? 'committed' : 'saved, ' + ((s.commit || {}).state || '')) : 'not saved: ' + esc(s.refused || s.status)}</li>`).join('') + '</ol>' : '');
  }
  return `<details class="b-panel b-revs" data-brevs="${esc(id)}"${revsOpen === id ? ' open' : ''}>` +
    '<summary>Revisions</summary>' + inner + '</details>';
}

function entryActions(e) {
  if (ed) return '';
  if (e.state === 'tombstone') {
    return '<p class="b-note">A retired entry is kept as it is; its history and revisions stay readable.</p>';
  }
  return '<div class="b-actions b-entryactions" role="group" aria-label="Change this entry">' +
    button('edit', 'Edit') + button('classify', 'Classify') +
    button('override', e.override ? 'Clear override' : 'Set override') +
    button('retire', 'Retire') + '</div>';
}

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
    ? `<p class="b-warn">Superseded by ${e.supersededBy.map(t => t.present ? entryLink(t) : esc(t.id)).join(', ')}.</p>`
    : !tomb && e.invalidated ? `<p class="b-warn">Invalidated with no replacement: ${esc(e.invalidated)}</p>` : '';
  const facts = '<dl class="b-facts">' +
    `<dt>Id</dt><dd class="mono">${esc(e.id)}</dd>` +
    `<dt>Kind</dt><dd>${esc(e.entryKind)}</dd>` +
    `<dt>Classification</dt><dd>${classChip(e.classification)} <span class="b-note">${esc(ABOUT[e.classification] || '')}</span></dd>` +
    (e.override ? `<dt>Override</dt><dd><span class="b-note">${esc(e.override)}</span></dd>` : '') +
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
  const editing = ed && ed.id === e.id ? formHtml(e) : '';
  return back + stale +
    `<article class="b-entry" aria-labelledby="btitle"><header class="b-head">` +
    `<p class="b-kicker">${chip('b-kind', e.entryKind)} ${classChip(e.classification)}` +
    `${tomb ? chip('b-c-removed', 'Removed') : ''}${e.override ? chip('b-override', 'Manual override') : ''}</p>` +
    `<h1 class="b-title" id="btitle" tabindex="-1">${esc(e.title)}</h1>` +
    (e.summary ? `<p class="b-lede">${esc(e.summary)}</p>` : '') +
    `<p class="b-permalink"><span class="mono">${esc(entryHref(e.id))}</span> ` +
    `<button type="button" data-copy-link="${esc(entryHref(e.id))}">Copy link</button></p></header>` +
    writeStatus(e.id) + entryActions(e) + editing + removed + superseded +
    `<div class="b-read"><div class="b-prose">${md(e.body, { base: 2 }) ||
      '<p class="b-none">This entry has no body.</p>'}</div>` +
    `<aside class="b-side" aria-label="About this entry"><div class="b-panel">${facts}</div>` +
    `<div class="b-panel"><h2>Classification history</h2>${history}</div>${revisionsHtml(e.id)}</aside></div>` +
    warnings + '</article>';
}

/* ── the writes ────────────────────────────────────────────────────── */

function payloadOf() {
  const d = ed.draft;
  const base = { request: ed.request };
  if (ed.mode === 'create' || ed.mode === 'edit') {
    const p = { ...base, title: d.title, kind: d.kind, summary: d.summary,
                sources: d.sources.split('\n').map(s => s.trim()).filter(Boolean),
                related: d.related.split(',').map(s => s.trim()).filter(Boolean), body: d.body };
    if (ed.mode === 'create') { p.classification = d.classification; p.reason = d.reason; }
    else { p.id = ed.id; p.expected = ed.base; }
    return p;
  }
  const p = { ...base, id: ed.id, expected: ed.base, reason: d.reason };
  if (ed.mode === 'classify') {
    p.classification = d.classification;
    if (d.classification === 'Superseded') {
      if (d.invalidate) p.invalidation = d.invalidation; else p.supersededBy = d.supersededBy;
    }
  }
  if (ed.mode === 'override') p.action = d.action;
  if (ed.mode === 'retire' && d.replacedBy) p.replacedBy = d.replacedBy;
  return p;
}

async function save() {
  if (!ed || ed.busy) return;
  if (ed.mode === 'retire') {
    const said = armed.click('retire', undefined, () => renderBrain(true));
    if (said === 'armed') { renderBrain(true); return; }
    if (said === 'busy') return;
    armed.disarm('retire');
  }
  ed.busy = true;
  ed.error = null;
  ed.lostReply = false;
  renderBrain(true);
  const mine = ed;
  const res = await post('/api/brain/' + mine.mode, payloadOf());
  mine.busy = false;
  if (ed !== mine) return;          // cancelled meanwhile: the answer is still recorded below
  if (res.ok) {
    const op = res.data.operation;
    ops[op.id] = { request: mine.request, op, at: Date.now() };
    ed = null;
    forget();
    toast(op.duplicate ? 'Already saved — this request was answered from the record.'
      : `Saved: ${op.what}` + (op.commit && op.commit.state === 'failed' ? ' — the commit failed' : ''),
      !!(op.commit && op.commit.state === 'failed'));
    if (mine.mode === 'create') { S.brainEntry = op.id; S.brainBad = null; syncRoute(); }
    renderBrain(true);
    // the form that held focus is gone: the entry's heading takes it (a
    // new entry's arrives with its reading, and is focused then)
    if (mine.mode !== 'create') $('#bshell .b-title')?.focus();
    return;
  }
  if (res.status === 0) {
    // no answer: ask the record before saying anything happened
    mine.lostReply = true;
    try {
      const r = await fetch('/api/brain/operation?request=' + encodeURIComponent(mine.request));
      const data = await r.json().catch(() => ({}));
      if (data.operation && data.operation.saved) {
        ops[data.operation.id] = { request: mine.request, op: data.operation, at: Date.now() };
        ed = null;
        forget();
        toast('The save had landed — the board\'s record says so.');
        if (mine.mode === 'create') { S.brainEntry = data.operation.id; syncRoute(); }
      }
    } catch (e) { /* the warning stands */ }
    renderBrain(true);
    return;
  }
  // any answer is final for this identity: the next Save is a new request
  mine.request = newRequest();
  if (res.data && res.data.stale) {
    mine.conflict = { error: res.error, current: res.data.current };
  } else {
    mine.error = res.error;
  }
  renderBrain(true);
  const focus = $('#bshell .b-conflict, #bshell .b-form [role="alert"]');
  if (focus) { focus.setAttribute('tabindex', '-1'); focus.focus(); }
}

async function retryOp(action, request) {
  const id = Object.keys(ops).find(k => ops[k].request === request);
  const res = await post('/api/brain/retry', { request, action });
  if (res.ok && id) ops[id].op = res.data.operation;
  toast(res.ok ? (action === 'commit' ? 'Commit retried.' : 'Push asked for.') : res.error, !res.ok);
  forget();
  renderBrain(true);
}

function currentEntry() {
  const slot = cache.entries[key()];
  return slot && slot.data && (slot.data.state === 'entry' || slot.data.state === 'tombstone') ? slot.data : null;
}

function act(name, arg) {
  const e = currentEntry();
  if (name === 'new') {
    startWrite('create', null);
    if (!cache.index) load('index');
  } else if (['edit', 'classify', 'override', 'retire'].includes(name) && e) {
    startWrite(name, e);
    if (!cache.index) load('index');
  } else if (name === 'cancel') {
    ed = null;
    armed.disarm('retire');
  } else if (name === 'write' && ed) ed.preview = false;
  else if (name === 'preview' && ed) ed.preview = true;
  else if (name === 'save') { save(); return; }
  else if (name === 'reread' && ed) {
    // the current version becomes the base; the person's draft stays
    const fresh = ed.conflict && ed.conflict.current;
    ed.base = fresh && fresh.revision ? fresh.revision : (e ? e.revision : ed.base);
    ed.conflict = null;
    ed.error = null;
    delete cache.entries[key()];
  } else if (name === 'retry-commit') { retryOp('commit', arg); return; }
  else if (name === 'retry-push') { retryOp('push', arg); return; }
  // a person's own action paints at once, wherever focus is: only a live
  // reading waits for the person to stop typing
  renderBrain(true);
  const target = $('#bshell .b-form input, #bshell .b-form textarea, #bshell .b-form select');
  if (ed && ['new', 'edit', 'classify', 'override', 'retire'].includes(name) && target) target.focus();
  if (!ed && name === 'cancel') $('#bshell .b-title')?.focus();
}

function wire(el) {
  if (wired) return;
  wired = true;
  el.addEventListener('click', (ev) => {
    const b = ev.target.closest && ev.target.closest('[data-bact]');
    if (!b || !el.contains(b) || b.disabled) return;
    act(b.dataset.bact, b.dataset.barg);
  });
  const onField = (ev) => {
    const f = ev.target;
    if (!ed || !f.dataset || !f.dataset.bf) return;
    const name = f.dataset.bf;
    ed.draft[name] = f.type === 'checkbox' ? f.checked : f.value;
    // a choice that changes which fields exist is drawn at once
    if (ev.type === 'change' && (name === 'classification' || name === 'invalidate')) renderBrain(true);
  };
  el.addEventListener('input', onField);
  el.addEventListener('change', onField);
  el.addEventListener('toggle', (ev) => {
    const d = ev.target;
    if (d.dataset && d.dataset.brevs) {
      revsOpen = d.open ? d.dataset.brevs : null;
      if (d.open) loadRevisions(d.dataset.brevs);
    }
  }, true);
  el.addEventListener('focusout', (ev) => { if (isField(ev.target)) setTimeout(() => renderBrain(), 0); });
  el.addEventListener('pointerdown', (ev) => { if (ev.isPrimary !== false) pressing().down(); });
  document.addEventListener('click', () => pressing().done(), true);
  document.addEventListener('pointerup', () => pressing().up(), true);
  document.addEventListener('pointercancel', () => pressing().done(), true);
}

function pressing() {
  if (!press) press = pressHold({ onRelease: () => renderBrain() });
  return press;
}
const isField = (n) => !!n && /^(INPUT|TEXTAREA|SELECT)$/.test(n.tagName || '');

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

function renderBrain(force) {
  const el = $('#bshell');
  if (!el) return;
  wire(el);
  const k = key();
  // a write belongs to the page it was started on: leaving it puts the
  // draft away (navigation never writes, and never carries a draft along)
  if (ed && ((ed.mode === 'create') !== (k === 'index') || (ed.id && k !== 'e:' + ed.id))) ed = null;
  // held while a person types, and while a pointer is down on the view
  if (!force && ((isField(document.activeElement) && el.contains(document.activeElement))
      || pressing().held())) return;
  let html;
  // a destination has not arrived until its reading has: an entry's own
  // markup may hold other loading notes (its Revisions fold), so this is
  // said, never searched for in the markup
  let loading = false;
  if (S.brainBad) {
    html = back + '<div class="b-panel" role="alert"><h1 class="b-title" tabindex="-1">Not a Brain address</h1>' +
      `<p><code>${esc(S.brainBad)}</code> does not name an entry. An entry's address is ` +
      '<code>/brain/e/&lt;id&gt;</code>, its id in lower-case letters, digits and hyphens.</p></div>';
  } else {
    const slot = k === 'index' ? cache.index : cache.entries[k];
    if (!slot || Date.now() - slot.at > FRESH_MS) load(k);
    if (k !== 'index') {
      const id = k.slice(2);
      const o = ops[id];
      const settling = o && o.op && o.op.saved && (o.op.push || {}).state === 'pending';
      if (settling && Date.now() - (o.at || 0) > FRESH_MS) loadOp(id);
      if (revsOpen === id && !cache.revisions[id]) loadRevisions(id);
    }
    loading = !slot;
    html = !slot ? '<p class="b-loading" role="status" aria-busy="true">Reading brain/…</p>'
      : k === 'index' ? (slot.data ? listHtml(slot)
        : `<div class="b-panel b-bad" role="alert"><h1 class="b-title" tabindex="-1">Brain</h1><p>` +
          `${esc(slot.error || 'brain/ could not be read')}</p></div>`)
      : entryHtml(slot, k.slice(2));
    if (k === 'index' && ed && ed.mode === 'create' && slot && slot.data) {
      html = back + formHtml(null);
    }
  }
  const arrived = k !== paintedKey && !loading;
  if (html !== painted) {
    // a live repaint keeps the reader's place: replaced only when the
    // reading changed, and focus is put back where it was
    const active = document.activeElement;
    const keep = active && el.contains(active) && active.dataset
      ? (active.dataset.brainId ? `[data-brain-id="${CSS.escape(active.dataset.brainId)}"]`
        : active.dataset.bact ? `[data-bact="${CSS.escape(active.dataset.bact)}"]`
          : active.dataset.bf ? `[data-bf="${CSS.escape(active.dataset.bf)}"]` +
            (active.type === 'radio' ? `[value="${CSS.escape(active.value)}"]` : '') : null) : null;
    el.innerHTML = `<div class="b-main">${html}</div>`;
    painted = html;
    if (S.brainEntry) settleLinks(el, S.brainEntry);
    if (keep) el.querySelector(keep)?.focus();
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
