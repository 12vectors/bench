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
  const started = Date.now();
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
  if (ed && ed.rereading && ed.id && k === 'e:' + ed.id) {
    // "Use the current version": the base is what a reading asked after
    // the click says is on disk, never a revision guessed earlier
    if (started < ed.rereading) { load(k); return; }
    settleReread(slot);
    // forced: the person is likely in a field, which holds a live repaint
    if (S.view === 'brain' && key() === k) renderBrain(true);
    return;
  }
  if (S.view === 'brain' && key() === k) renderBrain();
}

/* The reading asked for by "Use the current version" has landed: the
   draft stays, the base becomes the revision now on disk, and Save is
   offered again. A reading that failed says so, rather than leaving a
   base nobody has seen. */
function settleReread(slot) {
  const e = slot.data;
  ed.rereading = 0;
  if (!slot.error && e && (e.state === 'entry' || e.state === 'tombstone')) {
    ed.base = e.revision;
  } else {
    ed.error = 'the current version could not be read' +
      (slot.error ? ` (${slot.error})` : '') + '; use the current version again';
  }
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
    'work repository like the cards are. Ask the Brain Agent for one with <strong>New entry</strong> ' +
    '(it reads the project and proposes a draft you review and apply), write it yourself, or by hand ' +
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
    more + '</ul><p class="b-note">Nothing was merged or guessed, and a file listed here is not found by ' +
    'search. To fix one: <strong>invalid</strong> — correct the field named, following the version 1 ' +
    'record in AGENTS.md (“The Brain”); <strong>duplicate-id</strong> — give one of the files a new ' +
    '<code>**Id:**</code>; <strong>unsupported</strong> — this board does not read that ' +
    '<code>**Brain:**</code> version; <strong>unversioned</strong> — add <code>**Brain:** 1</code> or move ' +
    'the file out of <code>brain/</code>; <strong>refused</strong> — replace the link or special file with ' +
    'a plain Markdown file. It appears on the next reading.</p></details>';
}

/* ── finding an entry (task 159) ───────────────────────────────────────

   Search and filters over the index the board already sent: a plain,
   case-insensitive substring match over each entry's title, summary, id,
   kind, file name, sources and related ids, and two filters —
   classification and kind, independent of each other and of the Board's
   stages. Deterministic and bounded by the index (brain.py caps the files
   it reads); no model, no request. Kept across navigation, so coming back
   to the list finds it as it was left; not part of an address, which
   names a destination, never a moment. */
const find = { q: '', cls: 'all', kind: 'all' };

function finding() { return !!(find.q.trim() || find.cls !== 'all' || find.kind !== 'all'); }

function haystack(e) {
  return [e.title, e.summary, e.id, e.kind, e.file,
    ...(e.sources || []).map(s => s.text), ...(e.related || [])]
    .filter(Boolean).join('\n').toLowerCase();
}

function matches(e) {
  if (find.cls !== 'all' && e.classification !== find.cls) return false;
  if (find.kind !== 'all' && e.kind !== find.kind) return false;
  const words = find.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = haystack(e);
  return words.every(w => hay.includes(w));
}

function findHtml(index, hits) {
  const total = index.entries.length;
  const count = (list, pick) => list.filter(pick).length;
  const opt = (v, label, on) => `<option value="${esc(v)}"${on ? ' selected' : ''}>${esc(label)}</option>`;
  const classes = opt('all', `All classifications (${total})`, find.cls === 'all') + CLASSES.map(c =>
    opt(c, `${c} (${count(index.entries, e => e.classification === c)})`, find.cls === c)).join('');
  const kinds = opt('all', 'All kinds', find.kind === 'all') + KINDS.map(k =>
    opt(k, `${k} (${count(index.entries, e => e.kind === k)})`, find.kind === k)).join('');
  return `<form class="b-panel b-find" role="search" aria-label="Find Brain entries" data-bfindform>` +
    `<div class="b-findrow">` +
    `<label class="b-findq"><span>Search</span><input type="search" data-bfind="q" value="${esc(find.q)}" ` +
    `placeholder="Title, summary, id, source or related entry" autocomplete="off" spellcheck="false" ` +
    `aria-describedby="bfindcount"></label>` +
    `<label><span>Classification</span><select data-bfind="cls">${classes}</select></label>` +
    `<label><span>Kind</span><select data-bfind="kind">${kinds}</select></label>` +
    (finding() ? button('clear-find', 'Clear') : '') + `</div>` +
    `<p class="b-note" id="bfindcount" role="status" aria-live="polite">` +
    (finding() ? `${hits.length} of ${total} entr${total === 1 ? 'y' : 'ies'} match.`
      : `${total} entr${total === 1 ? 'y' : 'ies'}. Search reads this list only; it asks nothing of a model.`) +
    `</p></form>`;
}

function noResults(index) {
  const said = [find.q.trim() ? `“${find.q.trim()}”` : '',
    find.cls !== 'all' ? `classified ${find.cls}` : '', find.kind !== 'all' ? `of kind ${find.kind}` : '']
    .filter(Boolean).join(', ');
  return `<div class="b-panel b-empty" role="status"><p><strong>No entry matches</strong> ${esc(said)}.</p>` +
    `<p>Search looks at each entry's title, summary, id, kind, file name, sources and related ids. ` +
    `${index.diagnostics && index.diagnostics.length ? 'A file listed below as not shown cannot match until it is fixed. ' : ''}` +
    `Clear the search and filters to see all ${index.entries.length}, or write the entry you were looking for.</p>` +
    `<p>${button('clear-find', 'Clear search and filters')} ${button('ag-new', 'New entry')}</p></div>`;
}

function listHtml(slot) {
  const index = slot.data;
  const head = '<header class="b-head"><div class="b-headrow"><h1 class="b-title" tabindex="-1">Brain</h1>' +
    (index.directory.state !== 'refused' ? button('ag-new', 'New entry', { primary: true }) : '') + '</div>' +
    '<p class="b-lede">Maintained project knowledge kept in the work repository\'s ' +
    '<code>brain/</code>. Each entry has a stable address that survives a rename, a new title ' +
    'or a new classification. An entry explains; it does not override configuration, ' +
    'merge rules or a person\'s decision.</p></header>';
  const stale = slot.error ? `<p class="b-warn" role="status">${esc(slot.error)} — showing the last reading.</p>` : '';
  if (index.directory.state === 'refused') {
    return head + stale + `<div class="b-panel b-bad" role="alert"><p><strong>brain/ was not read.</strong> ` +
      `${esc(index.directory.why)}.</p></div>`;
  }
  if (!index.entries.length) {
    return head + stale + requestsHtml() + emptyGuidance(index.directory) + diagnosticsBlock(index);
  }
  const hits = index.entries.filter(matches);
  const filtering = finding();
  const shown = CLASSES.filter(c => find.cls === 'all' || find.cls === c);
  const sections = !hits.length ? noResults(index) : shown.map(c => {
    const rows = hits.filter(e => e.classification === c);
    // while a search or filter narrows the list, a classification with no
    // match is left out rather than drawn as an empty section
    if (filtering && !rows.length) return '';
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
  return head + stale + requestsHtml() + findHtml(index, hits) + sections + diagnosticsBlock(index) +
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
         lostReply: false, rereading: 0, title: e ? e.title : null };
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
  return '<div class="b-panel b-bad b-conflict" role="alert" tabindex="-1"><h2>Not saved: the entry changed</h2>' +
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
      rereadHtml() + conflictHtml() + body + errorHtml() +
      '<div class="b-actions">' + button('save', ph === 'armed' ? 'Confirm: retire this entry'
        : ed.busy ? 'Retiring…' : 'Retire', { alarm: true, armed: ph === 'armed', busy: ed.busy,
        disabled: ed.busy || !!ed.rereading }) +
      button('cancel', 'Cancel', { disabled: ed.busy }) +
      (ph === 'armed' ? '<span class="b-note" role="status">Click again to retire. It is kept as a tombstone.</span>' : '') +
      '</div></section>';
  }
  return `<section class="b-panel b-form" aria-labelledby="bform-h"><h2 id="bform-h">${labels[ed.mode]}</h2>` +
    // while the current version is being read, the reading on screen is
    // the old one: its revision says nothing about the base, so no warning
    (ed.rereading ? rereadHtml()
      : ed.mode !== 'create' && e && e.revision !== ed.base && !ed.conflict
      ? '<p class="b-warn" role="status">The entry changed on disk since you started. Saving will be ' +
        'refused; use the current version and keep your draft.' + button('reread', 'Use current version') + '</p>' : '') +
    conflictHtml() + body + errorHtml() +
    '<div class="b-actions">' + button('save', ed.busy ? 'Saving…' : 'Save', { primary: true, busy: ed.busy,
      disabled: ed.busy || !!ed.rereading }) +
    button('cancel', 'Cancel', { disabled: ed.busy }) + '</div></section>';
}

function rereadHtml() {
  return ed.rereading ? '<p class="b-note b-rereading" role="status" aria-busy="true">Reading the current ' +
    'version… Your draft is kept; Save is offered once it has been read.</p>' : '';
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
    (ag ? '' : button('ag-ask', 'Ask Brain Agent')) +
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

/* ── the Brain Agent (task 175) ───────────────────────────────────────

   A person asks; a launched, read-only agent asks back or proposes; the
   person reviews, edits and applies. The page holds one request at a
   time (`ag`): a composer before Generate, then the request the board
   keeps under `state/brain/drafts/`. Opening the composer reads the
   preflight (a GET: nothing launched); Generate and Continue are armed and
   each launch one bounded round; typing an answer sends nothing until
   Continue. Apply is armed and writes through the Brain's own write. */

let ag = null;
const drafts = { list: null, at: 0, one: {} };   // id -> {data, at}
const AG_STATUS = {
  running: 'Working', questions: 'Waiting for your answers', ready: 'Ready to continue',
  proposed: 'Draft ready for review', applying: 'Applying', applied: 'Applied',
  failed: 'Failed', cancelled: 'Cancelled', discarded: 'Discarded',
};

function agRequest() {
  return 'ba-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

async function loadDrafts() {
  if (inflight.has('drafts')) return;
  inflight.add('drafts');
  try {
    const res = await fetch('/api/brain/drafts');
    const data = await res.json().catch(() => ({}));
    if (res.ok) drafts.list = data;
  } catch (e) { /* kept as last read */ }
  drafts.at = Date.now();
  inflight.delete('drafts');
  renderBrain();
}

async function loadDraft(id) {
  const k = 'd:' + id;
  if (inflight.has(k)) return;
  inflight.add(k);
  try {
    const res = await fetch('/api/brain/drafts?id=' + encodeURIComponent(id));
    const data = await res.json().catch(() => ({}));
    drafts.one[id] = { data: data.draft || null, error: data.error || null, at: Date.now() };
  } catch (e) {
    drafts.one[id] = { ...(drafts.one[id] || {}), error: 'the board could not be asked', at: Date.now() };
  }
  inflight.delete(k);
  const d = drafts.one[id].data;
  if (ag && ag.draftId === id && d) seedEdit(d);
  renderBrain();
}

async function loadPreflight() {
  if (!ag || inflight.has('pf')) return;
  inflight.add('pf');
  const q = new URLSearchParams();
  if (ag.target) q.set('target', ag.target);
  if (ag.text) q.set('text', ag.text.slice(0, 2000));
  try {
    const res = await fetch('/api/brain/draft/preflight?' + q.toString());
    const data = await res.json().catch(() => ({}));
    if (ag) ag.preflight = data.preflight || { error: data.error || 'the board could not say' };
  } catch (e) {
    if (ag) ag.preflight = { error: 'the board could not be asked' };
  }
  inflight.delete('pf');
  renderBrain(true);
}

function openComposer(target, revision, title) {
  ag = { mode: 'compose', target: target || null, expected: revision || null, targetTitle: title || null,
         text: '', request: agRequest(), preflight: null, busy: false, error: null };
  loadPreflight();
}

function openDraft(id) {
  ag = { mode: 'draft', draftId: id, answers: {}, note: '', edit: null, view: 'rendered',
         busy: false, error: null, request: agRequest() };
  loadDraft(id);
}

function currentDraft() {
  return ag && ag.draftId && drafts.one[ag.draftId] ? drafts.one[ag.draftId].data : null;
}

/* The person's edit starts as the proposal, once per proposal round; a
   repaint or a re-read never overwrites what they typed. */
function seedEdit(d) {
  if (!ag || d.status !== 'proposed' || !d.proposal) return;
  if (ag.edit && ag.edit.round === d.proposalRound) return;
  const p = d.proposal;
  ag.edit = { round: d.proposalRound, title: p.title, kind: p.kind, summary: p.summary || '',
              body: p.body, sources: (p.sources || []).join('\n'), related: (p.related || []).join(', '),
              classification: p.classification || 'Unclassified',
              reason: p.classificationWhy || '' };
}

/* A line diff, bounded: the exact change against the entry this draft
   read. Equal runs are folded to their edges. */
function lineDiff(before, after) {
  const a = (before || '').split('\n').slice(0, 1500);
  const b = (after || '').split('\n').slice(0, 1500);
  const n = a.length, m = b.length;
  const w = new Array((n + 1) * (m + 1)).fill(0);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      w[i * (m + 1) + j] = a[i] === b[j] ? w[(i + 1) * (m + 1) + j + 1] + 1
        : Math.max(w[(i + 1) * (m + 1) + j], w[i * (m + 1) + j + 1]);
    }
  }
  const rows = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { rows.push([' ', a[i]]); i++; j++; }
    else if (j < m && (i >= n || w[i * (m + 1) + j + 1] >= w[(i + 1) * (m + 1) + j])) { rows.push(['+', b[j]]); j++; }
    else { rows.push(['-', a[i]]); i++; }
  }
  const out = [];
  rows.forEach((r, k) => {
    if (r[0] === ' ') {
      const near = rows.slice(Math.max(0, k - 2), k + 3).some(x => x[0] !== ' ');
      if (!near) { if (out[out.length - 1] !== '…') out.push('…'); return; }
    }
    out.push(r);
  });
  if (!rows.some(r => r[0] !== ' ')) return '<p class="b-note">No change to the body.</p>';
  return '<pre class="b-diff" aria-label="Changes to the body">' + out.map(r => r === '…'
    ? '<span class="b-diff-gap">…</span>'
    : `<span class="b-diff-${r[0] === '+' ? 'add' : r[0] === '-' ? 'del' : 'same'}">` +
      `${r[0] === '+' ? '+ ' : r[0] === '-' ? '− ' : '  '}${esc(r[1])}</span>`).join('\n') + '</pre>';
}

function preflightHtml(pf) {
  if (!pf) return '<p class="b-note" role="status" aria-busy="true">Reading what the Brain Agent would use…</p>';
  if (pf.error) return `<p class="b-warn" role="alert">${esc(pf.error)}</p>`;
  const sel = pf.selection || {};
  const model = sel.model && sel.model.value ? sel.model.value : 'harness default';
  const harness = (sel.harness || {}).name || pf.adapter || 'unknown';
  const blocked = pf.blockers && pf.blockers.length
    ? `<div class="b-panel b-bad" role="alert"><p><strong>The Brain Agent cannot be launched here.</strong> ` +
      `${esc(pf.blockers[0])}</p><p>Nothing will be launched or paid for. You can still write the entry yourself.</p></div>`
    : '';
  const w = pf.work || {};
  const app = pf.app || {};
  return blocked + '<details class="b-panel b-scope"><summary>What the Brain Agent will read, and what it may do</summary>' +
    '<dl class="b-facts">' +
    `<dt>Harness and model</dt><dd>${esc(harness)} · ${esc(model)}</dd>` +
    `<dt>Application</dt><dd><span class="mono">${esc(app.root || 'none')}</span>` +
    `${app.commit ? ` at <span class="mono">${esc(app.commit.slice(0, 12))}</span>` : ''}<br>` +
    `<span class="b-note">${esc(app.how || '')}. ${esc(app.note || '')}.</span></dd>` +
    `<dt>Work root</dt><dd><span class="mono">${esc(w.root || '')}</span><br><span class="b-note">${esc(String(w.sources || 0))} ` +
    `file(s) — ${esc(Object.entries(w.counts || {}).map(([k, v]) => `${v} from ${k}/`).join(', ') || 'none')} — ` +
    `${esc(w.how || '')}.${w.excludedCount ? ` ${esc(String(w.excludedCount))} left out (credential-shaped, links, oversized or over the bound).` : ''}</span></dd>` +
    `<dt>Never read</dt><dd class="b-note">${esc((pf.denied || {}).patterns ? pf.denied.patterns.join(', ') : '')}; ${esc((pf.denied || {}).private || '')}</dd>` +
    `<dt>Budget</dt><dd>${esc(String((pf.budget || {}).rounds))} rounds, ${esc(String(Math.round(((pf.budget || {}).secondsPerRound || 0) / 60)))} minutes each, ` +
    `at most ${esc(String((pf.budget || {}).questions))} questions a round</dd>` +
    `<dt>Authority</dt><dd>${esc(pf.authority || '')}. Enforced by ${esc(pf.enforcement || '')}.</dd>` +
    '</dl></details>';
}

function composerHtml() {
  const pf = ag.preflight;
  const related = pf && pf.related && pf.related.length && !ag.target
    ? '<div class="b-panel"><p><strong>Possibly the better home:</strong> an update keeps one coherent entry ' +
      'instead of another article. Choosing one switches this request to an update; nothing is written.</p><ul class="b-links">' +
      pf.related.map(r => `<li>${esc(r.title || r.id)} <span class="mono b-note">${esc(r.id)}</span> ` +
        button('ag-target', 'Update this instead', { arg: r.id }) + '</li>').join('') + '</ul></div>' : '';
  const ph = phaseOf(armed.store, 'ag-generate');
  const blocked = pf && pf.blockers && pf.blockers.length;
  return `<section class="b-panel b-form b-agent" aria-labelledby="bag-h"><h2 id="bag-h">` +
    `${ag.target ? `Ask Brain Agent about ${esc(ag.targetTitle || ag.target)}` : 'New entry'}</h2>` +
    `<p class="b-note">${ag.target
      ? 'Say what to correct or add. The agent proposes an update of this entry; you review the exact changes, edit them and apply.'
      : 'Say what this entry should capture. The Brain Agent reads the project, asks if it must, and proposes an entry you review, edit and apply.'}</p>` +
    field('ag-text', ag.target ? 'What should change' : 'What should this entry capture?',
      `<textarea id="bf-ag-text" data-ag="text" rows="4" aria-describedby="bh-ag-text">${esc(ag.text)}</textarea>`,
      ag.target ? 'For example: “The export now writes UTF-8 with a BOM — correct this.”'
        : 'For example: “Explain how verification works” or “Capture this architecture decision”.') +
    (ag.target ? '' : '<p class="b-note"><button type="button" class="b-link" data-bact="ag-manual">Write it yourself instead</button></p>') +
    preflightHtml(pf) + related +
    (ag.error ? `<p class="b-warn" role="alert">Not started: ${esc(ag.error)}</p>` : '') +
    '<div class="b-actions">' +
    button('ag-generate', ph === 'armed' ? 'Confirm: launch the Brain Agent' : ag.busy ? 'Starting…' : 'Generate',
      { primary: true, armed: ph === 'armed', busy: ag.busy, disabled: ag.busy || !ag.text.trim() || !!blocked }) +
    button('ag-close', 'Cancel', { disabled: ag.busy }) +
    (ph === 'armed' ? '<span class="b-note" role="status">Click again to launch one bounded, read-only run. It costs tokens.</span>' : '') +
    '</div></section>';
}

function progressHtml(d) {
  const p = d.progress || {};
  const tool = p.toolActivity;
  return '<div class="b-progress" aria-live="polite">' +
    (p.doing ? `<p><strong>Now:</strong> ${esc(p.doing)}</p>` : '') +
    (p.blocked ? `<p class="b-warn"><strong>Waiting on:</strong> ${esc(p.blocked)}</p>` : '') +
    '<div class="b-cols"><div><h3>Done</h3><ul>' + (p.done || []).map(x => `<li>${esc(x)}</li>`).join('') + '</ul></div>' +
    '<div><h3>Remaining</h3><ul>' + ((p.remaining || []).map(x => `<li>${esc(x)}</li>`).join('') || '<li>Nothing</li>') + '</ul></div></div>' +
    `<p class="b-note">Last update ${esc(p.lastUpdate && p.lastUpdate.at ? p.lastUpdate.at : '—')}` +
    (tool ? ` · last tool activity ${tool.last ? esc(new Date(tool.last * 1000).toLocaleTimeString()) : 'none yet'}` +
      (tool.inFlight ? ` (${esc(tool.inFlight)})` : '') + ' — activity, not progress' : '') + '</p></div>';
}

function conversationHtml(d) {
  const lines = (d.conversation || []).map(c => `<li class="b-conv-${esc(c.who)}"><span class="b-note mono">${esc(c.at || '')}</span> ` +
    `<strong>${c.who === 'you' ? 'You' : c.who === 'agent' ? 'Brain Agent' : 'Board'}</strong> · ${esc(c.kind)}: ${esc(c.text)}` +
    (c.questions ? '<ul>' + c.questions.map(q => `<li>${esc(q)}</li>`).join('') + '</ul>' : '') + '</li>');
  return '<details class="b-panel"><summary>Conversation (' + lines.length + ')</summary><ol class="b-history">' +
    lines.join('') + '</ol></details>';
}

function proposalHtml(d) {
  const p = d.proposal;
  const e = ag.edit || {};
  const target = d.target ? (cache.entries['e:' + d.target] || {}).data : null;
  const views = ['rendered', 'changes', 'edit'];
  const tabs = '<div class="b-tabs" role="group" aria-label="The draft">' + views.map(v =>
    `<button type="button" class="b-tab" data-bact="ag-view" data-barg="${v}" aria-pressed="${ag.view === v}">` +
    `${v === 'rendered' ? 'Draft' : v === 'changes' ? (d.mode === 'update' ? 'Changes' : 'Whole entry') : 'Edit'}</button>`).join('') + '</div>';
  let pane;
  if (ag.view === 'edit') {
    pane = field('ag-title', 'Title', `<input id="bf-ag-title" data-age="title" type="text" value="${esc(e.title)}" autocomplete="off">`) +
      field('ag-kind', 'Kind', `<select id="bf-ag-kind" data-age="kind">` + KINDS.map(k =>
        `<option${k === e.kind ? ' selected' : ''}>${esc(k)}</option>`).join('') + '</select>') +
      field('ag-summary', 'Summary', `<input id="bf-ag-summary" data-age="summary" type="text" value="${esc(e.summary)}" autocomplete="off">`) +
      field('ag-sources', 'Sources', `<textarea id="bf-ag-sources" data-age="sources" rows="3">${esc(e.sources)}</textarea>`) +
      field('ag-related', 'Related entries', `<input id="bf-ag-related" data-age="related" type="text" value="${esc(e.related)}" autocomplete="off">`) +
      field('ag-body', 'Body', `<textarea id="bf-ag-body" data-age="body" rows="16">${esc(e.body)}</textarea>`);
    if (d.mode === 'create') {
      pane += '<fieldset class="b-fieldset"><legend>Classification — the agent suggested ' +
        `${esc(p.classification)}; you decide</legend>` + ['Current', 'Historical', 'Unclassified'].map(c =>
        `<label class="b-radio"><input type="radio" name="bf-ag-class" data-age="classification" value="${c}"` +
        `${e.classification === c ? ' checked' : ''}> ${classChip(c)}</label>`).join('') + '</fieldset>' +
        field('ag-reason', 'Why this classification', `<input id="bf-ag-reason" data-age="reason" type="text" value="${esc(e.reason)}" autocomplete="off">`);
    } else {
      pane += `<p class="b-note">The entry's classification (${esc(target ? target.classification : 'as it is')}) and its history are kept. ` +
        `The agent suggested ${esc(p.classification)}; change it with Classify after applying, if you agree.</p>`;
    }
  } else if (ag.view === 'changes') {
    pane = d.mode === 'update'
      ? (target ? `<p class="b-note">Against ${esc(d.target)} as this draft read it (${esc(d.targetRevision || '')}).</p>` +
          (target.revision !== d.targetRevision ? '<p class="b-warn">The entry has changed since; applying will be refused.</p>' : '') +
          (e.title !== target.title ? `<p><strong>Title:</strong> <del>${esc(target.title)}</del> → <ins>${esc(e.title)}</ins></p>` : '') +
          lineDiff(target.body, e.body) : '<p class="b-note" role="status">Reading the entry…</p>')
      : lineDiff('', e.body);
  } else {
    pane = `<h3>${esc(e.title)}</h3>${e.summary ? `<p class="b-lede">${esc(e.summary)}</p>` : ''}` +
      `<div class="b-prose">${md(e.body || '', { base: 3 })}</div>`;
  }
  const claims = (p.claims || []).length ? '<h3>Claims, and how far each goes</h3><ul>' + p.claims.map(c =>
    `<li>${chip('b-claim b-claim-' + esc(c.status), c.status)} ${esc(c.statement)}` +
    `${c.sources.length ? ` <span class="b-note mono">${c.sources.map(esc).join(', ')}</span>` : ''}</li>`).join('') + '</ul>' : '';
  const sources = '<h3>Sources it cites, at the revision read</h3><ul class="b-links">' + (d.bindings || []).map(b =>
    `<li><span class="mono">${esc(b.ref)}</span> <span class="b-note mono">${b.commit ? 'commit ' + esc(b.commit.slice(0, 12)) + ', ' : ''}` +
    `sha256 ${esc(b.sha256.slice(0, 12))}</span>${b.why ? ` — ${esc(b.why)}` : ''}</li>`).join('') + '</ul>';
  const suggest = p.suggestTarget && d.mode === 'create'
    ? `<div class="b-panel" role="note"><p><strong>The agent suggests updating ${esc(p.suggestTarget)} instead.</strong> ` +
      `${esc(p.suggestWhy || '')}</p><p>Switching asks for a new draft of that entry; nothing is written or launched until you continue.</p>` +
      button('ag-retarget', phaseOf(armed.store, 'ag-retarget') === 'armed' ? `Confirm: update ${p.suggestTarget} instead` : `Update ${p.suggestTarget} instead`,
        { arg: p.suggestTarget, armed: phaseOf(armed.store, 'ag-retarget') === 'armed' }) + '</div>' : '';
  const conflict = d.conflict
    ? `<div class="b-panel b-bad" role="alert"><h3>Not applied: ${d.conflict.code === 'source-drift'
        ? 'a source changed' : 'the entry changed'}</h3>` +
      (d.conflict.sources ? '<ul>' + d.conflict.sources.map(s => `<li><span class="mono">${esc(s.ref)}</span> ${esc(s.why)}</li>`).join('') + '</ul>'
        : `<p>${esc(d.conflict.why || '')}</p>`) +
      '<p>Nothing was written. Ask for a revised draft (below) so the agent reads them again, or discard this draft.</p></div>' : '';
  const ap = phaseOf(armed.store, 'ag-apply');
  return suggest + conflict + tabs + `<div class="b-panel b-agdraft">${pane}</div>` +
    (p.uncertainty ? `<p class="b-warn"><strong>Uncertain:</strong> ${esc(p.uncertainty)}</p>` : '') + claims + sources +
    '<div class="b-actions">' +
    button('ag-apply', ap === 'armed' ? (d.mode === 'update' ? `Confirm: apply to ${d.target}` : 'Confirm: create this entry')
      : ag.busy ? 'Applying…' : 'Apply', { primary: true, armed: ap === 'armed', busy: ag.busy, disabled: ag.busy }) +
    button('ag-discard', phaseOf(armed.store, 'ag-discard') === 'armed' ? 'Confirm: discard' : 'Discard',
      { armed: phaseOf(armed.store, 'ag-discard') === 'armed', disabled: ag.busy }) +
    (ap === 'armed' ? `<span class="b-note" role="status">Click again to write ${d.mode === 'update' ? 'this update' : 'this entry'} ` +
      'through the Brain\'s own revision-guarded write.</span>' : '') + '</div>';
}

function continueHtml(d) {
  if (!d.canContinue) {
    return d.status === 'proposed' || d.status === 'questions' || d.status === 'failed'
      ? `<p class="b-note">This request has used its ${esc(String(d.maxRounds))} rounds: apply, edit and apply, or discard it.</p>` : '';
  }
  const qs = d.status === 'questions' ? (d.questions || []).map(q =>
    field('ag-q-' + q.id, esc(q.question), `<textarea id="bf-ag-q-${esc(q.id)}" data-agq="${esc(q.id)}" rows="2"` +
      `${q.why ? ` aria-describedby="bh-ag-q-${esc(q.id)}"` : ''}>${esc(ag.answers[q.id] || '')}</textarea>`,
      q.why ? esc(q.why) : '')).join('') : '';
  const label = d.status === 'questions' ? 'Anything else (optional)' : d.status === 'proposed'
    ? 'What should the revised draft change?' : 'A note for the next round (optional)';
  const ph = phaseOf(armed.store, 'ag-continue');
  const word = d.status === 'questions' ? 'Continue' : d.status === 'proposed' ? 'Ask for a revised draft' : 'Try again';
  return `<div class="b-panel b-continue"><h3>${d.status === 'questions' ? 'The Brain Agent asks' : word}</h3>` + qs +
    field('ag-note', label, `<textarea id="bf-ag-note" data-ag="note" rows="2">${esc(ag.note)}</textarea>`) +
    `<p class="b-note">Typing sends nothing. ${esc(word)} launches round ${d.round + 1} of ${d.maxRounds}.</p>` +
    '<div class="b-actions">' + button('ag-continue', ph === 'armed' ? `Confirm: ${word.toLowerCase()}` : ag.busy ? 'Starting…' : word,
      { primary: d.status !== 'proposed', armed: ph === 'armed', busy: ag.busy, disabled: ag.busy }) +
    (ph === 'armed' ? '<span class="b-note" role="status">Click again to launch the next round. It costs tokens.</span>' : '') +
    '</div></div>';
}

function draftHtml() {
  const slot = drafts.one[ag.draftId];
  const d = slot && slot.data;
  if (!d) {
    return `<section class="b-panel b-agent"><h2>Brain Agent</h2><p class="b-note" role="status" aria-busy="true">` +
      `${esc((slot && slot.error) || 'Reading the request…')}</p>${button('ag-close', 'Close')}</section>`;
  }
  const state = AG_STATUS[d.status] || d.status;
  const tone = { failed: ' b-bad', cancelled: '', discarded: '' }[d.status] || '';
  let body = progressHtml(d);
  if (d.status === 'running') {
    const ph = phaseOf(armed.store, 'ag-cancel');
    body += '<div class="b-actions">' + button('ag-cancel', ph === 'armed' ? 'Confirm: cancel' : 'Cancel',
      { armed: ph === 'armed', alarm: true }) + '</div>';
  } else if (d.status === 'proposed') {
    body += proposalHtml(d) + continueHtml(d);
  } else if (d.status === 'questions' || d.status === 'ready') {
    body += continueHtml(d) + '<div class="b-actions">' + button('ag-discard',
      phaseOf(armed.store, 'ag-discard') === 'armed' ? 'Confirm: discard' : 'Discard',
      { armed: phaseOf(armed.store, 'ag-discard') === 'armed' }) + '</div>';
  } else if (d.status === 'failed') {
    body += `<div class="b-panel b-bad" role="alert"><p><strong>${esc((d.error || {}).code || 'failed')}:</strong> ` +
      `${esc((d.error || {}).why || '')}</p>${(d.error || {}).problems ? '<ul>' + d.error.problems.map(x =>
        `<li>${esc(x)}</li>`).join('') + '</ul>' : ''}<p>Nothing was written.</p></div>` + continueHtml(d) +
      '<div class="b-actions">' + button('ag-discard', phaseOf(armed.store, 'ag-discard') === 'armed' ? 'Confirm: discard' : 'Discard',
        { armed: phaseOf(armed.store, 'ag-discard') === 'armed' }) + '</div>';
  } else if (d.status === 'applied') {
    const a = d.applied || {};
    body += `<p>Applied as <a href="${esc(entryHref(a.id))}" data-route data-brain-id="${esc(a.id)}">${esc(a.id)}</a> ` +
      `(${esc(a.what || '')}). Saved, committed and shared are shown on the entry.</p>`;
  } else {
    body += `<p class="b-note">${d.status === 'cancelled' ? 'Cancelled: nothing was written, and this request cannot be applied.'
      : 'Discarded: the Brain is unchanged.'}</p>`;
  }
  return `<section class="b-panel b-agent${tone}" aria-labelledby="bag-h" data-agdraft="${esc(d.id)}">` +
    `<h2 id="bag-h" tabindex="-1">Brain Agent · ${esc(state)}</h2>` +
    `<p class="b-note">${d.mode === 'update' ? `Updating <span class="mono">${esc(d.target)}</span>` : 'A new entry'} · ` +
    `round ${esc(String(d.round))} of ${esc(String(d.maxRounds))} · <span class="mono">${esc(d.id)}</span></p>` +
    `<blockquote class="b-request">${esc(d.text)}</blockquote>` + body +
    (ag.error ? `<p class="b-warn" role="alert">${esc(ag.error)}</p>` : '') +
    conversationHtml(d) +
    `<p class="b-note">${esc(d.local || '')}. ${button('ag-close', 'Close')}</p></section>`;
}

function agentHtml() {
  if (!ag) return '';
  return ag.mode === 'compose' ? composerHtml() : draftHtml();
}

function requestsHtml() {
  const list = drafts.list;
  if (!list || !list.drafts.length) return '';
  const open = list.drafts.filter(d => !['applied', 'cancelled', 'discarded'].includes(d.status));
  if (!open.length) return '';
  return '<section class="b-panel" aria-labelledby="bag-list"><h2 id="bag-list">Brain Agent requests ' +
    `<span class="b-count">${open.length}</span></h2><p class="b-note">Private to this board until applied.</p><ul class="b-list">` +
    open.map(d => `<li class="b-row"><div class="b-rowhead"><span>${esc(d.text.slice(0, 120))}</span>` +
      chip('b-kind', AG_STATUS[d.status] || d.status) + '</div>' +
      `<p class="b-meta mono">${esc(d.id)}${d.target ? ' · updating ' + esc(d.target) : ' · new entry'} ` +
      `${button('ag-open', 'Open', { arg: d.id })}</p></li>`).join('') + '</ul></section>';
}

async function agAct(name, arg) {
  if (name === 'ag-new') { openComposer(null); return true; }
  if (name === 'ag-ask') {
    const e = currentEntry();
    if (e) openComposer(e.id, e.revision, e.title);
    return true;
  }
  if (name === 'ag-manual') { ag = null; startWrite('create', null); if (!cache.index) load('index'); return true; }
  if (name === 'ag-close') { ag = null; armed.sweep(k => !k.startsWith('ag-')); return true; }
  if (name === 'ag-open') { openDraft(arg); return true; }
  if (name === 'ag-view' && ag) { ag.view = arg; return true; }
  if (name === 'ag-target' && ag) {
    const hit = ((ag.preflight || {}).related || []).find(r => r.id === arg);
    ag.target = arg; ag.targetTitle = hit ? hit.title : arg; ag.expected = null; ag.preflight = null;
    loadPreflight();
    return true;
  }
  const costly = { 'ag-generate': 1, 'ag-continue': 1, 'ag-apply': 1, 'ag-discard': 1, 'ag-cancel': 1, 'ag-retarget': 1 };
  if (!costly[name] || !ag) return false;
  const said = armed.click(name, undefined, () => renderBrain(true));
  if (said === 'armed') return true;
  if (said === 'busy') return true;
  armed.disarm(name);
  const d = currentDraft();
  let url, body;
  if (name === 'ag-generate') {
    url = '/api/brain/draft/start';
    body = { request: ag.request, text: ag.text, target: ag.target || undefined, expected: ag.expected || undefined };
  } else if (name === 'ag-continue' && d) {
    url = '/api/brain/draft/continue';
    body = { id: d.id, request: ag.request, round: d.round, answers: ag.answers, note: ag.note };
  } else if (name === 'ag-apply' && d && ag.edit) {
    const e = ag.edit;
    url = '/api/brain/draft/apply';
    body = { id: d.id, request: ag.request, round: d.proposalRound, title: e.title, kind: e.kind,
             summary: e.summary, body: e.body,
             sources: e.sources.split('\n').map(s => s.trim()).filter(Boolean),
             related: e.related.split(',').map(s => s.trim()).filter(Boolean),
             classification: e.classification, reason: e.reason };
  } else if (name === 'ag-discard' && d) {
    url = '/api/brain/draft/discard'; body = { id: d.id };
  } else if (name === 'ag-cancel' && d) {
    url = '/api/brain/draft/cancel'; body = { id: d.id };
  } else if (name === 'ag-retarget' && d) {
    url = '/api/brain/draft/retarget'; body = { id: d.id, round: d.round, target: arg };
  } else return true;
  ag.busy = true; ag.error = null;
  renderBrain(true);
  const mine = ag;
  let res = await post(url, body);
  if (res.status === 0 && (name === 'ag-generate' || name === 'ag-continue' || name === 'ag-apply')) {
    // no answer: the same request identity again is answered from the record
    res = await post(url, body);
  }
  mine.busy = false;
  if (ag !== mine) return true;
  if (!res.ok) {
    ag.error = res.error || 'refused';
    if (res.data && res.data.draft) drafts.one[res.data.draft.id] = { data: res.data.draft, at: Date.now() };
    if (ag.draftId) loadDraft(ag.draftId);
    renderBrain(true);
    return true;
  }
  ag.request = agRequest();
  const got = res.data.draft;
  if (got) drafts.one[got.id] = { data: got, at: Date.now() };
  if (name === 'ag-generate') { openDraft(got.id); drafts.one[got.id] = { data: got, at: Date.now() }; }
  if (name === 'ag-continue') { ag.answers = {}; ag.note = ''; }
  if (name === 'ag-apply' && res.data.operation) {
    const op = res.data.operation;
    ops[op.id] = { request: (got.applied || {}).request || '', op, at: Date.now() };
    forget();
    toast(`Applied: ${op.id} ${op.what}` + (op.commit && op.commit.state === 'failed' ? ' — the commit failed' : ''),
      !!(op.commit && op.commit.state === 'failed'));
  }
  drafts.at = 0;
  renderBrain(true);
  $('#bshell #bag-h')?.focus();
  return true;
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
  if (!ed || ed.busy || ed.rereading) return;
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
  if (name.startsWith('ag-')) {
    // the agent's own steps: what is synchronous (opening, switching a
    // view) has happened by the time this paints; a POST paints again
    // when its answer arrives
    agAct(name, arg);
    renderBrain(true);
    if (['ag-new', 'ag-ask', 'ag-target'].includes(name)) $('#bshell [data-ag="text"]')?.focus();
    if (name === 'ag-manual') $('#bshell .b-form input')?.focus();
    if (name === 'ag-close') $('#bshell .b-title')?.focus();
    return;
  }
  const e = currentEntry();
  if (name === 'clear-find') {
    find.q = ''; find.cls = 'all'; find.kind = 'all';
    renderBrain(true);
    $('#bshell [data-bfind="q"]')?.focus();
    return;
  }
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
    // the current version becomes the base once a reading asked now has
    // landed (load() settles it); the person's draft stays. Meanwhile the
    // form stays drawn — blanking the page into "Reading…" took the Save
    // button away — with Save held and a neutral note, never the stale
    // "Saving will be refused" warning the old reading would raise
    ed.conflict = null;
    ed.error = null;
    ed.rereading = Date.now();
    const slot = cache.entries[key()];
    if (slot) slot.at = 0;
    load(key());
  } else if (name === 'retry-commit') { retryOp('commit', arg); return; }
  else if (name === 'retry-push') { retryOp('push', arg); return; }
  // a person's own action paints at once, wherever focus is: only a live
  // reading waits for the person to stop typing
  renderBrain(true);
  const target = $('#bshell .b-form input, #bshell .b-form textarea, #bshell .b-form select');
  // the pressed "Use the current version" left with the conflict panel:
  // focus goes to the draft's first field, which outlives the reading
  if (ed && ['new', 'edit', 'classify', 'override', 'retire', 'reread'].includes(name) && target) target.focus();
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
    if (f.dataset && f.dataset.bfind) {
      // the list follows the search as it is typed: a person's own act,
      // painted at once with the caret kept where it was
      if (find[f.dataset.bfind] === f.value) return;
      find[f.dataset.bfind] = f.value;
      renderBrain(true);
      return;
    }
    if (ag && f.dataset && (f.dataset.ag || f.dataset.agq || f.dataset.age)) {
      // the Brain Agent's fields: kept here, sent nothing until a button
      if (f.dataset.ag === 'text') ag.text = f.value;
      else if (f.dataset.ag === 'note') ag.note = f.value;
      else if (f.dataset.agq) ag.answers[f.dataset.agq] = f.value;
      else if (f.dataset.age && ag.edit) ag.edit[f.dataset.age] = f.value;
      // Generate becomes available once there is a request; the preflight
      // is asked again for the words typed (a read) when the field is left
      if (ev.type === 'change' && f.dataset.ag === 'text' && ag.mode === 'compose') {
        if (!ag.target) loadPreflight();
        renderBrain(true);
      } else if (ev.type === 'input' && f.dataset.ag === 'text') {
        const gen = el.querySelector('[data-bact="ag-generate"]');
        const blocked = ag.preflight && ag.preflight.blockers && ag.preflight.blockers.length;
        if (gen) gen.disabled = ag.busy || !ag.text.trim() || !!blocked;
      } else if (ev.type === 'change' && f.dataset.age === 'classification') renderBrain(true);
      return;
    }
    if (!ed || !f.dataset || !f.dataset.bf) return;
    const name = f.dataset.bf;
    ed.draft[name] = f.type === 'checkbox' ? f.checked : f.value;
    // a choice that changes which fields exist is drawn at once
    if (ev.type === 'change' && (name === 'classification' || name === 'invalidate')) renderBrain(true);
  };
  el.addEventListener('input', onField);
  el.addEventListener('change', onField);
  // Enter in the search box searches as typing already did: nothing is sent
  el.addEventListener('submit', (ev) => { if (ev.target.matches('[data-bfindform]')) ev.preventDefault(); });
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
  // a request the Brain Agent is working on is read again while it works;
  // a request waiting on the person is not (it changes only when they act)
  setInterval(() => {
    if (S.view !== 'brain' || !ag || !ag.draftId) return;
    const slot = drafts.one[ag.draftId];
    const d = slot && slot.data;
    if (!d || ['running', 'applying'].includes(d.status)) loadDraft(ag.draftId);
  }, 2000);
}

function pressing() {
  // the held repaint runs after the click that released it has been
  // dispatched, never inside it: released synchronously from the click's
  // capture phase, it replaced the pressed button before the view's own
  // handler saw the click, and the click was lost (the Orchestrator's
  // release is scheduled the same way)
  if (!press) press = pressHold({ onRelease: () => setTimeout(() => renderBrain(), 0) });
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
    if (k === 'index' && (!drafts.list || Date.now() - drafts.at > 10000)) loadDrafts();
    if (ag && !ed) {
      // the Brain Agent's request is drawn wherever the person is in the
      // Brain: alone on the list's page, above the entry on an entry's
      const d = currentDraft();
      const target = d ? d.target : ag.target;
      if (target && !cache.entries['e:' + target]) load('e:' + target);
      html = k === 'index' ? back + agentHtml() : agentHtml() + html;
    }
  }
  const arrived = k !== paintedKey && !loading;
  if (html !== painted) {
    // a live repaint keeps the reader's place: replaced only when the
    // reading changed, and focus is put back where it was
    const active = document.activeElement;
    // the caret is kept in the search box and in a draft's text fields (a
    // forced repaint — the current version read in — lands while typing)
    const caret = active && active.dataset && (active.dataset.bfind === 'q' ||
      ((active.dataset.bf || active.dataset.ag || active.dataset.agq || active.dataset.age) &&
        (active.tagName === 'TEXTAREA' || active.type === 'text')))
      ? [active.selectionStart, active.selectionEnd] : null;
    const keep = active && el.contains(active) && active.dataset
      ? (active.classList.contains('b-conflict') ? '.b-conflict'
        : active.dataset.bfind ? `[data-bfind="${CSS.escape(active.dataset.bfind)}"]`
        : active.dataset.ag ? `[data-ag="${CSS.escape(active.dataset.ag)}"]`
        : active.dataset.agq ? `[data-agq="${CSS.escape(active.dataset.agq)}"]`
        : active.dataset.age && active.type !== 'radio' ? `[data-age="${CSS.escape(active.dataset.age)}"]`
        : active.dataset.brainId ? `[data-brain-id="${CSS.escape(active.dataset.brainId)}"]`
        : active.dataset.bact ? `[data-bact="${CSS.escape(active.dataset.bact)}"]`
          : active.dataset.bf ? `[data-bf="${CSS.escape(active.dataset.bf)}"]` +
            (active.type === 'radio' ? `[value="${CSS.escape(active.value)}"]` : '') : null) : null;
    el.innerHTML = `<div class="b-main">${html}</div>`;
    painted = html;
    if (S.brainEntry) settleLinks(el, S.brainEntry);
    if (keep) {
      const back = el.querySelector(keep);
      back?.focus();
      if (back && caret && back.setSelectionRange) back.setSelectionRange(caret[0], caret[1]);
    }
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
