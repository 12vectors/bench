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
import { activeSlot, closePanel, dropSlot, openAgent, registerAgent, renderPanel, savePanel, slotsOf } from '../lib/panel.js';
import { pressHold } from '../lib/press.js';
import { toast } from '../lib/toast.js';
import { evalField, evalHtml, openEvaluation, setEvalRepaint, wireEvaluate } from './evaluate.js';
import { recHtml, setRecHooks, wireReconcile } from './reconcile.js';
import { seedField, seedHtml, setSeedRepaint, wireSeed } from './seed.js';

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

// key -> {data, status, at, error}. `reviews` is the reviews filed on the
// board's cards (task 168); `entries` holds both entries (`e:<id>`) and
// reviews (`r:<card>/<review>`) as one page each
const cache = { index: null, reviews: null, handbook: null, entries: {}, revisions: {} };
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

function key() {
  // seeding the Brain (task 192): a page of its own, `/brain/seed`
  if (S.brainSeed) return 'seed';
  if (S.brainReview) return `r:${S.brainReview.card}/${S.brainReview.id}`;
  return S.brainEntry ? 'e:' + S.brainEntry : 'index';
}
const reviewHref = (card, id) => `/brain/r/${encodeURIComponent(card)}/${encodeURIComponent(id)}`;

// the handbook's write status, under a key no entry id can be
const HANDBOOK_OP = '@handbook';

function urlOf(k) {
  if (k === 'index') return '/api/brain';
  if (k === 'reviews') return '/api/brain/reviews';
  if (k === 'handbook') return '/api/brain/handbook';
  if (k.startsWith('r:')) {
    const [card, id] = k.slice(2).split('/');
    return '/api/brain/review?card=' + encodeURIComponent(card) + '&id=' + encodeURIComponent(id);
  }
  return '/api/brain/entry?id=' + encodeURIComponent(k.slice(2));
}

function slotOf(k) {
  return k === 'index' ? cache.index : k === 'reviews' ? cache.reviews
    : k === 'handbook' ? cache.handbook : cache.entries[k];
}
function newRequest() {
  return 'bw-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

async function load(k) {
  if (inflight.has(k)) return;
  inflight.add(k);
  const url = urlOf(k);
  const started = Date.now();
  let slot;
  try {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    slot = { status: res.status,
             data: k === 'index' ? data.brain : k === 'reviews' ? data.reviews
               : k === 'handbook' ? data.handbook
               : k.startsWith('r:') ? data.review : data.entry,
             error: data.error || null, at: Date.now() };
  } catch (e) {
    slot = { status: 0, data: null, error: 'the board could not be asked', at: Date.now() };
  }
  inflight.delete(k);
  // a failed re-read keeps what was last read, marked
  const old = slotOf(k);
  if (!slot.data && old && old.data) slot = { ...old, error: slot.error, at: slot.at };
  if (k === 'index') cache.index = slot;
  else if (k === 'reviews') cache.reviews = slot;
  else if (k === 'handbook') cache.handbook = slot;
  else cache.entries[k] = slot;
  if (k === 'reviews' || k === 'handbook') {
    if (S.view === 'brain' && key() === 'index') renderBrain();
    return;
  }
  if (ed && ed.rereading && ed.id && k === 'e:' + ed.id) {
    // "Use the current version": the base is what a reading asked after
    // the click says is on disk, never a revision guessed earlier
    if (started < ed.rereading) { load(k); return; }
    settleReread(slot);
    // forced: the person is likely in a field, which holds a live repaint
    if (S.view === 'brain' && key() === k) renderBrain(true);
    return;
  }
  // an entry read for the Brain Agent's diff repaints its panel wherever it is
  if (S.view === 'brain' && (key() === k || ag)) renderBrain();
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
  if (cache.reviews) cache.reviews.at = 0;
  if (cache.handbook) cache.handbook.at = 0;
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
  // seeding (task 192): choose and inspect the project's own sources for a
  // handbook. Opening it reads; it generates, launches and writes nothing
  const seed = '<p><a class="b-btn b-primary" href="/brain/seed" data-route>Seed Brain from this ' +
    'project</a> <span class="b-note">Choose which application and work files a handbook may be ' +
    'drafted from, and inspect exactly those.</span></p>';
  return `<div class="b-panel b-empty">${where}${seed}` +
    '<p>An entry is one Markdown file in <code>brain/</code> under the work root, committed to the ' +
    'work repository like the cards are. Ask the Brain Agent for one with <strong>New entry</strong> ' +
    '(it reads the project and proposes a draft you review and apply), write it yourself, or by hand ' +
    'following the version 1 format in AGENTS.md (“The Brain”); a file appears here as soon as it ' +
    'is saved.</p><p class="b-note">Reviews filed on cards appear here by themselves, as kind Review, ' +
    'read from their cards and never copied. Nothing imports reference/ files.</p></div>';
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

/* ── browsing the library (tasks 159, 183, 185) ────────────────────────────

   The list is organised by **kind** first: an All kinds view groups the
   entries by their kind, and choosing a kind narrows to it. Within a kind
   the classifications stay apart, each under its own heading — Current
   and Unclassified are shown by default, Historical and Superseded are
   hidden by default and shown by an explicit control. Hidden is never
   removed: counts say how many are hidden, an entry's address opens it
   whatever the view, and nothing here writes or reclassifies anything.

   Search is a plain, case-insensitive substring match of every word over
   each entry's title, summary, id, kind, file name, sources and related
   ids, composed with the kind and the classification controls.
   Deterministic and bounded by the index (brain.py caps the files it
   reads); no model, no request. Kept across navigation, so coming back
   to the list finds it as it was left; not part of an address, which
   names a destination, never a moment. */
const LIB_ORDER = ['Current', 'Unclassified', 'Historical', 'Superseded'];
const DEFAULT_SHOW = { Current: true, Unclassified: true, Historical: false, Superseded: false };
const find = { q: '', kind: 'all', show: { ...DEFAULT_SHOW } };
let navOpen = false;     // the compact controls on a narrow window

function finding() {
  return !!(find.q.trim() || find.kind !== 'all' ||
    LIB_ORDER.some(c => find.show[c] !== DEFAULT_SHOW[c]));
}

function resetFind() { find.q = ''; find.kind = 'all'; find.show = { ...DEFAULT_SHOW }; }

function haystack(e) {
  return [e.title, e.summary, e.id, e.kind, e.file,
    ...(e.sources || []).map(s => s.text), ...(e.related || [])]
    .filter(Boolean).join('\n').toLowerCase();
}

function searchHit(e) {
  const words = find.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = haystack(e);
  return words.every(w => hay.includes(w));
}

const shownClass = (c) => !!find.show[c];
const entries = (n) => `${n} ${n === 1 ? 'entry' : 'entries'}`;

// the six kinds of the contract, and any other a reading carries (none
// should: brain.py refuses another kind, but a list never hides a row)
function kindsOf(index) {
  const extra = [...new Set(index.entries.map(e => e.kind))].filter(k => !KINDS.includes(k)).sort();
  return KINDS.concat(extra);
}

/* One reading of the list's numbers, each with a stated scope:
   `searched` — every entry the search matches (all kinds, all
   classifications); `inKind` — those of the selected kind; `visible` —
   those the classification controls show. */
function tally(index) {
  const searched = index.entries.filter(searchHit);
  const inKind = find.kind === 'all' ? searched : searched.filter(e => e.kind === find.kind);
  const visible = inKind.filter(e => shownClass(e.classification));
  return { searched, inKind, visible };
}

function hiddenWords(list) {
  return LIB_ORDER.filter(c => !shownClass(c))
    .map(c => [c, list.filter(e => e.classification === c).length]).filter(([, n]) => n)
    .map(([c, n]) => `${n} ${c}`).join(', ');
}

function viewWords() {
  const shown = LIB_ORDER.filter(shownClass);
  return (find.kind === 'all' ? 'All kinds' : find.kind) + ' · ' +
    (shown.length ? shown.join(', ') : 'no classification shown') +
    (find.q.trim() ? ` · “${find.q.trim()}”` : '');
}

function navHtml(index, t) {
  const total = index.entries.length;
  const kindButton = (k, label, shown, all) => {
    const on = find.kind === k;
    return `<li><button type="button" class="b-kindbtn${on ? ' on' : ''}" data-bkind="${esc(k)}" ` +
      `aria-pressed="${on}" aria-label="${esc(label)}: ${shown} shown of ${entries(all)}">` +
      `<span class="b-kindname">${esc(label)}</span><span class="b-kindcount">${shown}` +
      `${shown !== all ? `<span class="b-of"> of ${all}</span>` : ''}</span></button></li>`;
  };
  const shownIn = (k) => t.searched.filter(e => e.kind === k && shownClass(e.classification)).length;
  const kindList = kindButton('all', 'All kinds', t.searched.filter(e => shownClass(e.classification)).length, total) +
    kindsOf(index).map(k => kindButton(k, k, shownIn(k), index.entries.filter(e => e.kind === k).length)).join('');
  const boxes = LIB_ORDER.map(c => {
    const n = t.inKind.filter(e => e.classification === c).length;
    return `<label class="b-show"><input type="checkbox" data-bshow="${c}"${shownClass(c) ? ' checked' : ''}> ` +
      `${classChip(c)} <span class="b-showcount">${n}</span>` +
      (DEFAULT_SHOW[c] ? '' : '<span class="b-note b-showdef">hidden by default</span>') + '</label>';
  }).join('');
  return `<aside class="b-nav${navOpen ? '' : ' b-navclosed'}" id="bnav" aria-label="Browse and filter the Brain">` +
    `<button type="button" class="b-navtoggle" data-bact="toggle-nav" aria-expanded="${navOpen}" ` +
    `aria-controls="bnavbody"><span class="b-navlab">Browse and filter</span>` +
    `<span class="b-navnow">${esc(viewWords())}</span></button>` +
    `<div class="b-navbody" id="bnavbody">` +
    `<form class="b-find" role="search" aria-label="Find Brain entries" data-bfindform>` +
    `<label class="b-findq"><span class="b-navh">Search</span><input type="search" data-bfind="q" value="${esc(find.q)}" ` +
    `placeholder="Title, summary, id, source…" autocomplete="off" spellcheck="false" ` +
    `aria-describedby="bfindcount"></label></form>` +
    `<nav class="b-kinds" aria-labelledby="bkinds-h"><h2 class="b-navh" id="bkinds-h">Kind</h2>` +
    `<ul>${kindList}</ul><p class="b-note">Each count: entries shown, of all entries of that kind.</p></nav>` +
    `<fieldset class="b-shows"><legend class="b-navh">Show classifications</legend>${boxes}` +
    `<p class="b-note">Counts: ${find.kind === 'all' ? 'all kinds' : esc(find.kind)}` +
    `${find.q.trim() ? ', matching the search' : ''}. Hiding changes no entry.</p></fieldset>` +
    (finding() ? `<p class="b-navreset">${button('clear-find', 'Reset to default view')}</p>`
      : '<p class="b-note b-navreset">Default view: all kinds, Current and Unclassified.</p>') +
    `</div></aside>`;
}

/* The reviews filed on the board's cards (task 168), as rows of the
   library: kind Review, their classification (Unclassified where no
   record exists), found by search like any entry. A row is a link to the
   review's page; the report itself is read from its card there. */
function reviewRows() {
  const d = cache.reviews && cache.reviews.data;
  if (!d || !Array.isArray(d.reviews)) return [];
  return d.reviews.map(r => ({
    id: r.key, title: r.title, kind: 'Review', classification: r.classification,
    summary: `${r.kindWord}${r.rounds > 1 ? ` ${r.round} of ${r.rounds}` : ''} · ` +
      `${r.verdict || 'no verdict line'} · ${r.reviewer || 'reviewer not recorded'} · ${r.at}` +
      (r.archived ? ' · card archived' : ''),
    file: r.file, sources: [{ text: r.card ? `#${r.card}` : r.file }],
    related: [r.review], tombstone: false, override: r.override, review: r,
  }));
}

function rowHtml(e) {
  const link = e.review
    ? `<a href="${esc(e.review.address)}" data-route data-brain-id="${esc(e.id)}">${esc(e.title)}</a>`
    : entryLink(e);
  return `<li class="b-row${e.tombstone ? ' b-tomb' : ''}${e.review ? ' b-reviewrow' : ''}"><div class="b-rowhead">${link}` +
    classChip(e.classification) + (e.tombstone ? chip('b-c-removed', 'Removed') : '') +
    (e.review && !e.review.classified ? (e.review.classificationSource === 'closed-ticket'
      ? chip('b-note', 'closed ticket', 'Historical because its card is in Done or the archive — a lifecycle default, not a person\'s decision')
      : chip('b-note', 'not yet classified', 'no classification is recorded for this review')) : '') +
    (e.override ? chip('b-override', 'Manual override', 'a person fixed this classification') : '') +
    (e.review && e.review.indexed === false ? chip('b-note', 'not indexed',
      'on its card and listed here; this board\'s review index does not list it yet') : '') +
    '</div>' +
    (e.summary ? `<p class="b-sum">${esc(e.summary)}</p>` : '<p class="b-sum b-none">No summary.</p>') +
    `<p class="b-meta mono">${esc(e.id)}${e.sources.length ? ' · from ' +
      e.sources.slice(0, 3).map(s => esc(s.text)).join(', ') : ''}</p></li>`;
}

/* One kind: its classifications apart, each under its own heading, and
   what the controls hide said in numbers, never dropped silently. */
function kindSection(index, k, t) {
  const all = index.entries.filter(e => e.kind === k);
  const searched = t.searched.filter(e => e.kind === k);
  const rows = searched.filter(e => shownClass(e.classification));
  const hiddenList = searched.filter(e => !shownClass(e.classification));
  const slug = k.replace(/[^A-Za-z0-9-]/g, '');
  let body;
  if (!all.length) {
    body = `<p class="b-none">No ${esc(k)} entries in this Brain yet. Choose another kind, or write one with <strong>New entry</strong>.</p>`;
  } else if (!rows.length) {
    body = `<p class="b-none">${find.q.trim() && !searched.length
      ? `No ${esc(k)} entry matches the search.`
      : `No ${esc(k)} entry is shown with the classifications chosen.`}</p>`;
  } else {
    body = LIB_ORDER.filter(shownClass).map(c => {
      const list = rows.filter(e => e.classification === c);
      if (!list.length) return '';
      return `<div class="b-group" data-bgroup="${c}"><h3 id="bgrp-${slug}-${c}">${classChip(c)} ` +
        `<span class="b-count" aria-label="${entries(list.length)}">${list.length}</span>` +
        `<span class="b-note b-groupnote">${esc(ABOUT[c])}</span></h3>` +
        `<ul class="b-list" aria-labelledby="bgrp-${slug}-${c}">${list.map(rowHtml).join('')}</ul></div>`;
    }).join('');
  }
  const hid = hiddenList.length
    ? `<p class="b-hidden" data-bhidden="${esc(k)}">${esc(hiddenWords(hiddenList))} ` +
      `${hiddenList.length === 1 ? 'entry is' : 'entries are'} hidden here, kept and not deleted — ` +
      'show them with <strong>Show classifications</strong>.</p>' : '';
  return `<section class="b-panel b-kind" id="bkind-${slug}" data-bkindsec="${esc(k)}" aria-labelledby="bkindh-${slug}">` +
    `<h2 id="bkindh-${slug}">${esc(k)} <span class="b-count" aria-label="${rows.length} shown of ${entries(all.length)}">` +
    `${rows.length}${rows.length !== all.length ? ` of ${all.length}` : ''}</span></h2>${body}${hid}</section>`;
}

function noResults(index, t) {
  const said = [find.q.trim() ? `“${find.q.trim()}”` : '', find.kind !== 'all' ? `of kind ${find.kind}` : '']
    .filter(Boolean).join(', ');
  const hiddenHere = t.inKind.filter(e => !shownClass(e.classification));
  const why = hiddenHere.length
    ? `<p>${esc(hiddenWords(hiddenHere))} ${hiddenHere.length === 1 ? 'entry matches' : 'entries match'} ` +
      'but the classification controls hide ' + (hiddenHere.length === 1 ? 'it' : 'them') + '.</p>'
    : `<p>Search looks at each entry's title, summary, id, kind, file name, sources and related ids. ` +
      `${index.diagnostics && index.diagnostics.length ? 'A file listed below as not shown cannot match until it is fixed.' : ''}</p>`;
  // the default view has nothing to reset to: where the controls hide every
  // match (a Brain of history alone, say) the way on is to show them
  const actions = [
    hiddenHere.length ? button('show-hidden', 'Show hidden classifications') : '',
    finding() ? button('clear-find', 'Reset to default view') : '',
    button('ag-new', 'New entry')].filter(Boolean).join(' ');
  return `<div class="b-panel b-empty" role="status"><p><strong>No entry matches</strong>` +
    `${said ? ' ' + esc(said) : ' the classifications chosen'}.</p>${why}` +
    (finding() ? `<p>Reset to the default view of the ${entries(index.entries.length)}, or write the entry you were looking for.</p>`
      : '<p>This is the default view. Show the hidden classifications, or write the entry you were looking for.</p>') +
    `<p>${actions}</p></div>`;
}

/* What the reviews' reading could not account for: a classification
   recorded for a review this board does not have, and a record that
   could not be read — each said, nothing merged or guessed. */
function reviewsNote() {
  const slot = cache.reviews;
  if (!slot) return '<p class="b-note" role="status">Reading the reviews filed on cards…</p>';
  if (!slot.data) return `<p class="b-warn" role="status">The reviews filed on cards could not be read: ${esc(slot.error || 'no answer')}.</p>`;
  const d = slot.data;
  const rows = [];
  for (const o of d.orphans || []) {
    rows.push(`<li><a href="${esc(o.address)}" data-route>#${esc(o.card)} ${esc(o.review)}</a> — classified ` +
      `${esc(o.classification)} in <code>brain/${esc(o.file)}</code>, but ` +
      (o.why === 'card-missing' ? 'no card on this board has that number' : 'the card no longer holds that review') +
      '. The record is kept.</li>');
  }
  for (const x of d.diagnostics || []) {
    rows.push(`<li><code>brain/reviews/${esc(x.file)}</code> — <strong>${esc(x.code)}</strong>: ${esc(x.message)}</li>`);
  }
  const stale = slot.error ? `<p class="b-warn" role="status">${esc(slot.error)} — showing the last reading of the reviews.</p>` : '';
  return stale + (rows.length ? `<details class="b-panel b-diag"><summary>${rows.length} review record` +
    `${rows.length === 1 ? '' : 's'} could not be matched or read</summary><ul>${rows.join('')}</ul></details>` : '');
}

/* Filing and the review index (task 169): the reports this board did not
   file, filed reports whose commit or push did not happen, reviews the
   index does not list and indexed reviews whose source has gone — each
   said apart, with its next step. Retry and Rebuild are armed writes;
   everything else here is the server's reading. The index and the
   retained reports are this board's own: another board has neither. */
const STEP_WORDS = { file: 'Retry filing', commit: 'Retry commit', push: 'Retry push',
                     resolve: 'Close, leaving it unfiled' };
const rfReasons = {};

function rfButton(action, op, label) {
  const k = `rf-${action}:${op}`;
  const ph = phaseOf(armed.store, k);
  return button(`rf-${action}`, ph === 'armed' ? `Confirm: ${label.toLowerCase()}` : ph === 'busy' ? `${label}…` : label,
    { arg: op, armed: ph === 'armed', busy: ph === 'busy' });
}

function filingItem(o) {
  const what = `${o.runName || 'A run'}'s ${String(o.kind || 'report').toLowerCase()} on ` +
    (o.number ? `<a href="/cards/${esc(o.number)}?tab=activity" data-route>#${esc(o.number)}</a>` : esc(o.card));
  const words = o.status === 'filed'
    ? `<strong>Filed on its card</strong>; ${o.commit && o.commit.state === 'unknown'
      ? 'the board stopped before its commit was recorded'
      : `its commit failed${o.commit && o.commit.detail ? ` (${esc(o.commit.detail)})` : ''}`}.`
    : o.status === 'conflict' ? '<strong>Not filed — conflict.</strong>'
    : o.status === 'pending' ? '<strong>Not filed yet</strong> — the filing was interrupted.'
    : '<strong>Not filed.</strong>';
  const kept = o.retained ? ` The report (${esc(String(o.bytes || 0))} bytes${o.clipped ? ', clipped when it was filed' : ''}) ` +
    'is retained on this board only, under its original heading.' : '';
  let actions = '';
  if (o.status === 'filed') {
    actions = rfButton('commit', o.operation, 'Retry commit');
  } else if (o.status === 'conflict' || o.code === 'card-missing' || o.code === 'board-changed' ||
             o.code === 'ambiguous') {
    actions = (o.status !== 'conflict' && o.code !== 'board-changed' ? rfButton('file', o.operation, 'Retry filing') : '') +
      `<label class="b-rfreason">Why leave it unfiled <input type="text" data-rfreason="${esc(o.operation)}" ` +
      `value="${esc(rfReasons[o.operation] || '')}" maxlength="300"></label>` +
      rfButton('resolve', o.operation, 'Close');
  } else {
    actions = rfButton('file', o.operation, 'Retry filing');
  }
  return `<li data-rf="${esc(o.operation)}"><p>${what}: ${words}${o.detail ? ` ${esc(o.detail)}.` : ''}${kept}</p>` +
    (o.next ? `<p class="b-note">Next: ${esc(o.next)}.</p>` : '') +
    `<p class="b-note mono">“${esc(o.heading)}” · ${esc(o.operation)} · ${esc(String(o.attempts))} attempt` +
    `${o.attempts === 1 ? '' : 's'}</p><div class="b-actions">${actions}</div></li>`;
}

function filingHtml() {
  const d = cache.reviews && cache.reviews.data;
  const f = d && d.filing;
  if (!f) return '';
  const idx = f.index || {};
  const attention = f.attention || [];
  const missing = f.missingSources || [];
  // a board with no review on any card and nothing to recover has nothing
  // to say here, and no control to offer
  if (!attention.length && !missing.length && !(d.reviews || []).length) return '';
  const parts = [];
  if (attention.length) {
    parts.push(`<h3>Reports that need you (${attention.length})</h3><ul class="b-rflist">` +
      attention.map(filingItem).join('') + '</ul>');
  }
  if (missing.length) {
    parts.push(`<h3>Indexed reviews whose source has gone (${f.missingCount})</h3>` +
      '<p class="b-note">The index listed these, and no card on this board holds them now — a card ' +
      'deleted, or a report removed by hand. The index keeps them, marked; nothing is recreated.</p><ul>' +
      missing.map(m => `<li class="mono">${m.card ? `#${esc(m.card)}` : esc(m.file)} ${esc(m.review)}</li>`).join('') +
      '</ul>');
  }
  const idxWords = idx.state === 'absent' ? 'This board has no review index yet'
    : idx.state === 'unreadable' ? 'This board\'s review index could not be read'
    : `The review index lists ${idx.entries} review${idx.entries === 1 ? '' : 's'}` +
      (idx.built ? `, last written ${esc(idx.built)}${idx.builtBy === 'rebuild' ? ' by a rebuild' : ' by filing'}` : '');
  const unindexed = f.unindexed || 0;
  const rebuild = rfButton('rebuild', idx.revision || 'none', 'Rebuild index');
  const lead = `<p>${idxWords}. ${unindexed ? `<strong>${unindexed} review${unindexed === 1 ? ' is' : 's are'} ` +
    'not indexed</strong> — still listed below, read from their cards.' : 'Every review on the cards is indexed.'}</p>` +
    '<p class="b-note">Rebuilding reads every card and writes this board\'s index again. It changes no card ' +
    'and no classification record, and keeps a lost source marked rather than dropping it.</p>' +
    `<div class="b-actions">${rebuild}</div>`;
  // open on its own only when a person is needed; an index that is merely
  // missing or behind hides nothing, so its fold waits to be opened
  const open = attention.length || missing.length;
  const counts = (attention.length ? `${attention.length} need you` : 'nothing needs you') +
    (unindexed ? ` · ${unindexed} not indexed` : '') + (missing.length ? ` · ${f.missingCount} source${f.missingCount === 1 ? '' : 's'} gone` : '');
  return `<details class="b-panel b-filing"${open ? ' open' : ''}><summary>Report filing and the review index — ` +
    `${esc(counts)}</summary>${parts.join('')}${lead}<p class="b-note">Retained reports and the index are ` +
    'local to this board: a board that synced the cards has neither, and says so.</p></details>';
}

async function rfAct(name, arg) {
  const action = name.slice(3);
  const k = `${name}:${arg}`;
  const said = armed.click(k, undefined, () => renderBrain(true));
  if (said !== 'fire') { renderBrain(true); return; }
  armed.fire(k, () => { toast('No answer from the board — read the status again before retrying.', true); renderBrain(true); });
  renderBrain(true);
  const res = action === 'rebuild'
    ? await post('/api/brain/reviews/rebuild', { expected: arg })
    : await post('/api/reports/filing/retry', { operation: arg, action,
      ...(action === 'resolve' ? { reason: rfReasons[arg] || '' } : {}) });
  armed.settle(k);
  if (res.ok && action === 'rebuild') {
    const r = res.data.rebuild;
    toast(`Index rebuilt: ${r.reviews} review${r.reviews === 1 ? '' : 's'}` +
      (r.missing ? `, ${r.missing} lost source${r.missing === 1 ? '' : 's'} kept marked` : '') + '.');
  } else if (res.ok) {
    const o = res.data.operation;
    toast(o.duplicate ? `${STEP_WORDS[action]}: already done — nothing was written again.`
      : o.status === 'filed' ? (action === 'file' ? 'Filed on its card.' : `${STEP_WORDS[action]}: ${o.commit ? o.commit.state : 'done'}.`)
      : `${STEP_WORDS[action]}: ${o.status}${o.detail ? ` — ${o.detail}` : ''}.`, o.status !== 'filed' && o.status !== 'resolved');
  } else toast(res.error, true);
  if (cache.reviews) cache.reviews.at = 0;
  load('reviews');
}

function listHtml(slot) {
  const index = { ...slot.data, entries: slot.data.entries.concat(reviewRows()) };
  const head = '<header class="b-head"><div class="b-headrow"><h1 class="b-title" tabindex="-1">Brain</h1>' +
    (index.directory.state !== 'refused' ? button('ag-new', 'New entry', { primary: true }) : '') + '</div>' +
    '<p class="b-lede">Maintained project knowledge kept in the work repository\'s ' +
    '<code>brain/</code>, organised by kind, and the reviews filed on cards, read from the cards ' +
    'themselves. Each has a stable address that survives a rename, ' +
    'a new title or a new classification. An entry explains; it does not override configuration, ' +
    'merge rules or a person\'s decision.</p></header>';
  const stale = slot.error ? `<p class="b-warn" role="status">${esc(slot.error)} — showing the last reading.</p>` : '';
  if (index.directory.state === 'refused') {
    return head + stale + `<div class="b-panel b-bad" role="alert"><p><strong>brain/ was not read.</strong> ` +
      `${esc(index.directory.why)}.</p></div>`;
  }
  if (!index.entries.length) {
    return head + stale + requestsHtml() + handbookHtml(false) + adoptHtml() + emptyGuidance(index.directory) + filingHtml() +
      diagnosticsBlock(index) + reviewsNote();
  }
  const t = tally(index);
  const total = index.entries.length;
  const hiddenAll = t.inKind.filter(e => !shownClass(e.classification));
  const status = `<div class="b-libstatus"><p id="bfindcount" role="status" aria-live="polite">` +
    `Showing ${t.visible.length} of ${entries(total)} · ${esc(viewWords())}.` +
    (hiddenAll.length ? ` ${esc(hiddenWords(hiddenAll))} hidden by the classification controls — ` +
      'kept, not deleted.' : '') + '</p>' +
    (finding() ? button('clear-find', 'Reset to default view') : '') + '</div>';
  // a chosen kind with no entries at all says so in its own section; any
  // other view that shows nothing explains what matched and what is hidden
  const emptyKind = find.kind !== 'all' && !index.entries.some(e => e.kind === find.kind);
  const kinds = find.kind === 'all'
    ? kindsOf(index).filter(k => t.searched.some(e => e.kind === k))
    : [find.kind];
  const results = !t.visible.length && !emptyKind
    ? noResults(index, t)
    : kinds.map(k => kindSection(index, k, t)).join('');
  // reviews filed on cards are listed in the library and are not entries
  // a section can link
  const linkable = slot.data.entries.some(e => !e.tombstone);
  // evaluating classifications (task 196) follows the library, so the
  // reading and Tab order from New entry to the library's search is kept
  return head + stale + requestsHtml() + handbookHtml(linkable) +
    adoptHtml() + '<h2 class="b-libh">All entries</h2>' + `<div class="b-lib">` + navHtml(index, t) +
    `<div class="b-results">${status}${filingHtml()}${results}${diagnosticsBlock(index)}${reviewsNote()}` +
    `<p class="b-note mono">brain/ revision ${esc(index.revision)} · ${entries(total)}</p></div></div>` +
    evalHtml(slot.data.entries) + recHtml();
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

/* ── the curated handbook (task 171) ──────────────────────────────────

   Five sections linking existing entries by their stable ids, read from
   `GET /api/brain/handbook` and drawn above the library, which stays as
   it was. Linking an entry never classifies it: each link is drawn as
   what its own record says — the operative Current explanation, a
   convention, a Historical source, or missing with its remedy — beside
   the source authority the entry records. Organizing is one section at a
   time, one Save, bound to the record's revision and a request identity
   like every Brain write. */

const HB_STATE = {
  operative: ['Current — operative', 'b-c-current'],
  convention: ['Convention — not approved policy', 'b-c-unclassified'],
  historical: ['Historical — a source, not operative', 'b-c-historical'],
  superseded: ['Superseded', 'b-c-superseded'],
  unassessed: ['Unclassified — not assessed', 'b-c-unclassified'],
  retired: ['Retired', 'b-c-superseded'],
  missing: ['Missing', 'b-c-superseded'],
  duplicate: ['Duplicate id', 'b-c-superseded'],
  invalid: ['Invalid entry file', 'b-c-superseded'],
};
const COVER = { empty: 'Not covered yet', 'not-covered': 'No operative explanation',
                partial: 'Partly covered', covered: 'Covered' };
const STANDING = { implemented: 'Implemented', deployed: 'Deployed',
                   'agreed-future': 'Agreed future work', unresolved: 'Unresolved' };
const AUTH = { current: 'bound sources unchanged', stale: 'a bound source has moved',
               contradictory: 'sources disagree', unsourced: 'no source revision bound',
               unchecked: 'not every binding was checked' };
const BINDING = { current: 'unchanged since', stale: 'changed since', missing: 'gone now',
                  'absent-at-revision': 'not there at', 'off-branch': 'not in this history',
                  'unknown-revision': 'unknown revision', 'no-repository': 'no repository',
                  'not-checked': 'not checked', present: 'card on this board',
                  recorded: 'recorded by a person, not verified' };
let hb = null;   // organizing one section: {section, draft, base, reason, add, request, busy, error, conflict, lostReply}

function hbTitle(id, section) {
  const it = section && section.items.find(i => i.id === id);
  if (it && it.title) return it.title;
  const idx = cache.index && cache.index.data;
  const e = idx && idx.entries.find(x => x.id === id);
  return e ? e.title || id : id;
}

function authorityLine(a) {
  if (!a) return '';
  const parts = [
    a.standing ? `<strong>${esc(STANDING[a.standing] || a.standing)}</strong>`
      : '<span>standing not stated</span>',
    esc(a.deployment.message),
    `<span class="b-auth b-auth-${esc(a.state)}">${esc(AUTH[a.state] || a.state)}</span>`,
    a.assessed ? `assessed ${esc(a.assessed)}` : 'assessment date not recorded',
  ];
  return `<p class="b-meta">${parts.join(' · ')}</p>` +
    (a.contradictions.length ? '<ul class="b-hbprob">' + a.contradictions.map(c =>
      `<li>${esc(c)}</li>`).join('') + '</ul>' : '');
}

function hbItemHtml(i) {
  const [label, cls] = HB_STATE[i.state] || [i.state, ''];
  const name = i.address
    ? `<a href="${esc(i.address)}" data-route data-brain-id="${esc(i.id)}">${esc(i.title || i.id)}</a>`
    : `<span class="mono">${esc(i.id)}</span>`;
  return `<li class="b-hbitem b-hb-${esc(i.state)}"><div class="b-rowhead">${name} ${chip(cls, label)}` +
    (i.kind ? ` <span class="b-meta">${esc(i.kind)}</span>` : '') + '</div>' +
    (i.state !== 'operative' ? `<p class="b-note">${esc(i.message)}` +
      (i.remedy ? ` <span class="b-remedy">Remedy: ${esc(i.remedy)}.</span>` : '') + '</p>' : '') +
    (i.summary ? `<p class="b-sum">${esc(i.summary)}</p>` : '') + authorityLine(i.authority) + '</li>';
}

function hbOrganizeHtml(s) {
  const idx = cache.index && cache.index.data;
  const all = idx ? idx.entries.filter(e => !e.tombstone) : [];
  const n = hb.draft.length;
  const options = (value, except) => '<option value="">— choose an entry —</option>' +
    all.filter(e => !except.includes(e.id) || e.id === value).map(e =>
      `<option value="${esc(e.id)}"${e.id === value ? ' selected' : ''}>${esc(e.title || e.id)} · ` +
      `${esc(e.classification)} (${esc(e.id)})</option>`).join('');
  const rows = hb.draft.map((id, k) => {
    const t = hbTitle(id, s);
    const small = (act, label, aria, disabled) =>
      `<button type="button" class="b-btn" data-bact="${act}" data-barg="${k}" aria-label="${esc(aria)}"` +
      `${disabled || hb.busy ? ' disabled' : ''}>${label}</button>`;
    return `<li class="b-hborg"><span class="b-hbpos mono">${k + 1}</span>` +
      `<span class="b-hbname">${esc(t)} <span class="b-meta mono">${esc(id)}</span></span>` +
      '<span class="b-hbctl">' +
      small('hb-up', '↑ Up', `Move ${t} up`, k === 0) + small('hb-down', '↓ Down', `Move ${t} down`, k === n - 1) +
      `<label class="b-hbrep"><span class="b-note">Replace with</span> <select data-hbrep="${k}" ` +
      `aria-label="Replace ${esc(t)} with"${hb.busy ? ' disabled' : ''}>${options('', hb.draft)}</select></label>` +
      small('hb-remove', 'Remove', `Remove ${t} from ${s.title}`, false) + '</span></li>';
  }).join('');
  const conflict = hb.conflict ? (() => {
    const cur = (hb.conflict.current && hb.conflict.current.sections || []).find(x => x.id === s.id);
    const now = cur ? cur.items.map(i => i.id) : [];
    const list = (ids) => ids.length ? '<ol>' + ids.map(id => `<li>${esc(hbTitle(id, cur || s))} ` +
      `<span class="mono b-meta">${esc(id)}</span></li>`).join('') + '</ol>' : '<p class="b-none">No links.</p>';
    return '<div class="b-panel b-bad b-conflict" role="alert" tabindex="-1"><h3>Not saved: the handbook changed</h3>' +
      `<p>${esc(hb.conflict.error)}</p><p>Your order is kept. Compare, then save again on the current ` +
      'version; nothing is retried for you.</p><div class="b-compare">' +
      `<div><h4>What you tried to save</h4>${list(hb.draft)}</div>` +
      `<div><h4>${esc(s.title)} on disk now <span class="mono b-note">` +
      `${esc((hb.conflict.current && hb.conflict.current.record || {}).revision || 'unknown')}</span></h4>${list(now)}</div></div>` +
      `<div class="b-actions">${button('hb-rebase', 'Use the current version and keep my order', { primary: true })}` +
      `${button('hb-cancel', 'Discard my change')}</div></div>`;
  })() : '';
  return `<div class="b-hbedit" role="group" aria-label="Organize ${esc(s.title)}">` + conflict +
    (n ? `<ol class="b-hborglist">${rows}</ol>` : '<p class="b-none">No entries linked in this section.</p>') +
    '<div class="b-field b-hbadd"><label for="hb-add">Add an existing entry</label>' +
    `<div class="b-hbaddrow"><select id="hb-add" data-hb="add"${hb.busy ? ' disabled' : ''}>${options(hb.add, hb.draft)}</select>` +
    button('hb-add', 'Add to section', { disabled: hb.busy || !hb.add }) + '</div>' +
    (all.length ? '' : '<p class="b-note">There are no entries to link yet: write one with New entry.</p>') +
    '<p class="b-note">Linking an entry does not classify it. A Historical or Unclassified entry stays what it ' +
    'is, and is shown here as not the operative explanation.</p></div>' +
    '<div class="b-field"><label for="hb-reason">Why this change</label>' +
    `<input id="hb-reason" data-hb="reason" type="text" value="${esc(hb.reason)}" autocomplete="off" ` +
    'aria-describedby="hb-reason-h" required>' +
    '<p class="b-note" id="hb-reason-h">Required. Kept with your name in the handbook\'s history.</p></div>' +
    (hb.lostReply ? '<p class="b-warn" role="alert">No answer came back, and nothing is known to have been ' +
      'saved. Save again sends the same request; the board answers it from its record.</p>' : '') +
    (hb.error ? `<p class="b-warn" role="alert">Not saved: ${esc(hb.error)}</p>` : '') +
    '<div class="b-actions">' + button('hb-save', hb.busy ? 'Saving…' : `Save ${s.title}`,
      { primary: true, busy: hb.busy, disabled: hb.busy }) +
    button('hb-cancel', 'Cancel', { disabled: hb.busy }) + '</div></div>';
}

function hbSectionHtml(s, editable) {
  const c = s.coverage;
  const open = hb && hb.section === s.id;
  return `<section class="b-hbsec b-hbsec-${esc(c.state)}" aria-labelledby="hbh-${esc(s.id)}" data-hbsec="${esc(s.id)}">` +
    `<div class="b-headrow"><h3 id="hbh-${esc(s.id)}">${esc(s.title)}</h3> ` +
    `${chip(c.state === 'covered' ? 'b-c-current' : c.state === 'partial' ? 'b-c-unclassified'
      : 'b-c-superseded', COVER[c.state] || c.state)}` +
    (!open && editable ? button('hb-organize', 'Organize', { arg: s.id, disabled: !!hb }) : '') + '</div>' +
    `<p class="b-note">For ${esc(s.purpose)}.</p>` +
    (open ? hbOrganizeHtml(s)
      : (s.items.length ? '<ol class="b-list b-hblist">' + s.items.map(hbItemHtml).join('') + '</ol>'
        : `<p class="b-none">${esc(c.message)}</p>`) +
        (c.state !== 'empty' && c.state !== 'covered' ? `<p class="b-note">${esc(c.message)}</p>` : '') +
        (c.notes.length ? '<ul class="b-hbnotes">' + c.notes.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>' : '')) +
    '</section>';
}

function handbookHtml(linkable) {
  const slot = cache.handbook;
  const head = '<h2 id="hb-h">Project handbook</h2>';
  if (!slot) return `<section class="b-panel b-hb" aria-labelledby="hb-h">${head}<p class="b-loading" role="status" ` +
    'aria-busy="true">Reading the handbook…</p></section>';
  const h = slot.data;
  if (!h) return `<section class="b-panel b-hb b-bad" aria-labelledby="hb-h">${head}<p role="alert">` +
    `${esc(slot.error || 'the handbook could not be read')}</p></section>`;
  const rec = h.record;
  // organizing links existing entries: with none to link, there is
  // nothing to offer but the one action that writes the first entry
  const editable = (rec.state === 'record' || rec.state === 'missing') && (linkable || hb);
  const stale = slot.error ? `<p class="b-warn" role="status">${esc(slot.error)} — showing the last reading.</p>` : '';
  const recNote = rec.state === 'refused'
    ? `<p class="b-warn" role="alert"><code>${esc(rec.file)}</code> was not read: ${esc(rec.why)}. Organizing is off until it is fixed.</p>`
    : rec.state === 'invalid' || rec.state === 'unsupported'
      ? `<div class="b-warn" role="alert"><p><code>${esc(rec.file)}</code> does not follow the handbook format, so the ` +
        'board will not write over it. Fix it by hand:</p><ul>' + rec.problems.map(p => `<li>${esc(p)}</li>`).join('') + '</ul></div>'
      : '';
  const sum = h.summary;
  const history = h.history.length
    ? `<details class="b-hbhist"><summary>Handbook history (${h.history.length})</summary><ol class="b-history">` +
      h.history.slice().reverse().map(x => `<li>${esc(x.replace(/^-\s+/, ''))}</li>`).join('') + '</ol></details>' : '';
  return `<section class="b-panel b-hb" aria-labelledby="hb-h">${head}` +
    '<p class="b-lede">The curated landing: five sections linking maintained entries by their stable ids. ' +
    'A link does not classify an entry, and Current is not shipped: each entry says what it stands for and ' +
    'which source revisions back it, as a person recorded them. Reading this runs no check and reads no ' +
    'file content — only brain/ and local git history.</p>' +
    `<p class="b-note" role="status">${sum.covered} of ${sum.sections} sections covered · ${sum.empty} empty · ` +
    `<span class="mono">${esc(rec.file)}</span> revision <span class="mono">${esc(rec.revision)}</span></p>` +
    stale + recNote + writeStatus(HANDBOOK_OP) +
    (!linkable && !hb ? '<p class="b-note">The handbook links existing entries: write one first, ' +
      'then organize its sections here.</p>' : '') +
    `<div class="b-hbsecs">${h.sections.map(s => hbSectionHtml(s, editable)).join('')}</div>${history}</section>`;
}

/* An entry's own source authority, from its record (task 171): what it
   stands for, its policy, its bindings and what local git says of each,
   and every gap and contradiction — and the handbook sections that link it. */
function authorityHtml(e) {
  const a = e.authority;
  if (!a) return '';
  const binding = (b) => {
    const where = b.type === 'review'
      ? `<a href="${esc(b.address)}" data-route>review ${esc(b.target)}</a>`
      : `<span class="mono">${esc(b.type)}:${esc(b.target)}</span>`;
    return `<li>${where} <span class="mono b-meta">@${esc((b.revision || '').slice(0, 12))}</span> ` +
      `<span class="b-auth b-auth-${esc(b.state)}">${esc(BINDING[b.state] || b.state)}</span>` +
      (b.message ? `<span class="b-note"> — ${esc(b.message)}</span>` : '') + '</li>';
  };
  const member = (e.handbook || []).length
    ? '<ul class="b-links">' + e.handbook.map(m => `<li>${esc(m.title)} — ${m.position} of ${m.of}</li>`).join('') + '</ul>'
    : '<span class="b-none">Not linked from the handbook.</span>';
  return '<div class="b-panel b-authority"><h2>Source authority</h2><dl class="b-facts">' +
    `<dt>Standing</dt><dd>${a.standing ? esc(STANDING[a.standing] || a.standing)
      : '<span class="b-none">not stated — Current is not a claim that it shipped</span>'}</dd>` +
    `<dt>Deployment</dt><dd>${esc(a.deployment.message)}</dd>` +
    `<dt>Policy</dt><dd>${a.policy === 'approved' ? 'Approved working policy' : a.policy === 'inferred'
      ? 'Inferred convention — not approved policy' : '<span class="b-none">not stated</span>'}` +
      (a.policyNote ? ` <span class="b-note">— ${esc(a.policyNote)}</span>` : '') + '</dd>' +
    `<dt>Assessed</dt><dd>${a.assessed ? esc(a.assessed) : '<span class="b-none">not recorded</span>'}</dd>` +
    `<dt>Bindings</dt><dd>${a.bindings.length ? '<ul class="b-links">' + a.bindings.map(binding).join('') + '</ul>'
      : '<span class="b-none">None. A plain source or link proves no revision.</span>'}</dd>` +
    `<dt>Reading</dt><dd><span class="b-auth b-auth-${esc(a.state)}">${esc(AUTH[a.state] || a.state)}</span></dd>` +
    `<dt>Handbook</dt><dd>${member}</dd></dl>` +
    (a.contradictions.length ? '<p class="b-warn" role="note">Disagreement, not resolved by bench:</p><ul>' +
      a.contradictions.map(c => `<li>${esc(c)}</li>`).join('') + '</ul>' : '') +
    (a.gaps.length ? '<ul class="b-hbnotes">' + a.gaps.map(g => `<li>${esc(g)}</li>`).join('') + '</ul>' : '') +
    '</div>';
}

/* ── adopting reference/ material (task 172) ──────────────────────────

   Nothing here is read until a person opens the panel, and opening it,
   choosing a file and comparing write nothing. Apply takes two presses and
   sends the exact source digest and destination revision the preview
   showed; the server reads both again. One file becomes one entry. */

let ad = null;   // {items, error, name, preview, op, draft, request, busy, confirm, error2, lost}
const ADOPT_OP = '@adopt';
const AD_STATE = { ready: 'Not adopted', catalogued: 'Catalogued', changed: 'Changed since adopted',
                   excluded: 'Excluded', unsupported: 'Cannot be adopted', conflict: 'Conflict',
                   retired: 'Entry retired', missing: 'Missing', 'source-missing': 'Source missing' };
const AD_OP = { catalogue: 'Catalogue the retained file',
                explanation: 'Write a maintained explanation',
                refresh: 'Record the new bytes' };
const adBytes = (n) => n == null ? '—' : n < 1024 ? `${n} B`
  : n < 1048576 ? `${(n / 1024).toFixed(1)} KiB` : `${(n / 1048576).toFixed(1)} MiB`;

async function adLoad() {
  try {
    const res = await fetch('/api/brain/adoption');
    const data = await res.json().catch(() => ({}));
    if (!ad) return;
    ad.inventory = data.adoption || null;
    ad.listError = data.adoption ? null : (data.error || 'reference/ could not be read');
  } catch (e) {
    if (ad) ad.listError = 'the board could not be asked';
  }
  renderBrain(true);
}

async function adPreview(name) {
  const mine = ad;
  try {
    const res = await fetch('/api/brain/adoption/preview?name=' + encodeURIComponent(name));
    const data = await res.json().catch(() => ({}));
    if (ad !== mine || mine.name !== name) return;
    mine.preview = data.preview || null;
    mine.previewError = data.preview ? null : (data.error || 'the file could not be read');
  } catch (e) {
    if (ad === mine) mine.previewError = 'the board could not be asked';
  }
  if (ad !== mine) return;
  const choices = (mine.preview && mine.preview.choices) || [];
  const keep = choices.find(c => c.op === mine.op && c.available);
  adChoose((keep || choices.find(c => c.available) || {}).op || null, !!keep);
  renderBrain(true);
}

/* A choice's fields start from the preview; what a person typed for this
   same choice is kept across a re-read. */
function adChoose(op, keepDraft) {
  ad.op = op;
  ad.confirm = false;
  const c = ((ad.preview && ad.preview.choices) || []).find(x => x.op === op);
  if (!c) { ad.draft = null; return; }
  if (keepDraft && ad.draft) return;
  const d = c.defaults || {};
  ad.draft = { title: d.title || '', summary: d.summary || '', kind: d.kind || 'Guide',
               body: d.body || '', reason: '' };
  ad.request = newRequest();
}

function adItemHtml(i) {
  const held = i.catalogue || i.explanation;
  return `<li class="b-aditem" data-aditem="${esc(i.name)}"><div class="b-adrow">` +
    `<button type="button" class="b-btn" data-bact="ad-pick" data-barg="${esc(i.name)}" ` +
    `aria-label="Preview ${esc(i.name)}">${esc(i.name)}</button>` +
    `<span class="b-chip b-ad-${esc(i.state)}">${esc(AD_STATE[i.state] || i.state)}</span>` +
    `<span class="b-note">${esc(i.type || 'unread')} · ${esc(adBytes(i.size))} · ` +
    `${i.inboundCount} inbound link${i.inboundCount === 1 ? '' : 's'} · owner ` +
    `${i.owner.state === 'known' ? esc(i.owner.name) : 'unknown'}</span></div>` +
    (i.why ? `<p class="b-note">${esc(i.why)}</p>` : '') +
    (held ? `<p class="b-note">Entry: <a href="${esc(entryHref(held.id))}" data-route ` +
      `data-brain-id="${esc(held.id)}">${esc(held.title || held.id)}</a></p>` : '') + '</li>';
}

function adPreviewHtml() {
  const p = ad.preview;
  const head = `<div class="b-headrow"><h3>${esc(ad.name)}</h3>${button('ad-back', 'Back to the list')}</div>`;
  if (ad.previewError) return head + `<p class="b-warn" role="alert">${esc(ad.previewError)}</p>`;
  if (!p) return head + '<p class="b-loading" role="status" aria-busy="true">Reading the file…</p>';
  const facts = '<dl class="b-facts">' +
    `<dt>Type</dt><dd>${esc(p.type || 'unread')}</dd><dt>Size</dt><dd>${esc(adBytes(p.size))}</dd>` +
    `<dt>SHA-256</dt><dd class="mono b-adhash">${esc(p.sha256 || 'withheld')}</dd>` +
    `<dt>Address</dt><dd>${p.address ? `<a href="${esc(p.address)}" target="_blank" rel="noopener noreferrer">` +
      `${esc(p.address)}</a> <span class="b-note">stays exactly as it is</span>` : '<span class="b-none">none</span>'}</dd>` +
    `<dt>Owner</dt><dd>${p.owner && p.owner.state === 'known' ? esc(p.owner.name) +
      (p.owner.dirty ? ' <span class="b-note">— uncommitted changes on disk</span>' : '')
      : '<span class="b-none">unknown</span>'}</dd>` +
    `<dt>Inbound links</dt><dd>${(p.inbound || []).length ? '<ul class="b-links">' + p.inbound.map(h =>
      `<li>${esc(h.kind)}: ${h.address ? `<a href="${esc(h.address)}">${esc(h.name)}</a>` : esc(h.name)}</li>`).join('') +
      '</ul>' + (p.inboundCount > p.inbound.length ? `<span class="b-note">and ${p.inboundCount - p.inbound.length} more</span>` : '')
      : '<span class="b-none">none found</span>'}</dd>` +
    ((p.citedBy || []).length ? `<dt>Cited by</dt><dd>${p.citedBy.map(entryLink).join(', ')}</dd>` : '') +
    '</dl>';
  if (!p.choices.length) {
    return head + facts + `<p class="b-warn" role="alert">${esc(p.why || 'This file cannot be adopted.')} ` +
      'Nothing was written.</p>';
  }
  const radios = '<fieldset class="b-adops"><legend>What should adopting it do?</legend>' +
    p.choices.map(c => `<label class="b-adop"><input type="radio" name="ad-op" data-adop="${esc(c.op)}" ` +
      `value="${esc(c.op)}"${ad.op === c.op ? ' checked' : ''}${c.available ? '' : ' disabled'}> ` +
      `<span>${esc(AD_OP[c.op])}${c.default ? ' <span class="b-note">(default)</span>' : ''}` +
      (c.role ? ` <span class="b-note">on the ${esc(c.role)} entry</span>` : '') +
      (c.why ? `<br><span class="b-note">${esc(c.why)}</span>` : '') + '</span></label>').join('') + '</fieldset>';
  return head + facts + radios + adFormHtml(p);
}

function adFormHtml(p) {
  const c = p.choices.find(x => x.op === ad.op);
  if (!c || !ad.draft) return '<p class="b-note">No choice is available for this file now.</p>';
  const d = ad.draft;
  const field = (name, label, value) => `<label class="b-field"><span>${label}</span>` +
    `<input type="text" data-ad="${name}" value="${esc(value)}"></label>`;
  let form = '';
  if (ad.op === 'refresh') {
    form = '<p class="b-note">Records that a person read the changed file. The entry\'s text, ' +
      'classification and override stay as they are.</p>' +
      `<p class="b-note mono b-adhash">recorded ${esc(c.recorded)}<br>on disk&nbsp; ${esc(p.sha256)}</p>` +
      (c.previousAvailable && p.sourceText !== null ? bodyDiff(p.previousText, p.sourceText)
        : '<p class="b-note">The earlier bytes are not available to compare, or this file is not text.</p>') +
      field('reason', 'Why this is being recorded (required)', d.reason);
  } else {
    form = field('title', 'Title', d.title) + field('summary', 'Summary (optional)', d.summary);
    if (ad.op === 'catalogue') {
      form += '<p class="b-note">Creates one <strong>Attachment</strong> entry linking this file. The file is ' +
        'not copied, moved or changed. It starts <strong>Unclassified</strong>: a person assesses it afterwards.</p>';
    } else {
      form += '<label class="b-field"><span>Kind</span><select data-ad="kind">' +
        ['Guide', 'Investigation', 'Decision'].map(k =>
          `<option value="${k}"${d.kind === k ? ' selected' : ''}>${k}</option>`).join('') + '</select></label>' +
        `<label class="b-field"><span>Explanation (Markdown)</span><textarea data-ad="body" rows="12">${esc(d.body)}</textarea></label>` +
        '<p class="b-note">A separate entry that cites the retained file and its catalogue entry. It starts ' +
        '<strong>Unclassified</strong>; the original stays at its address.</p>' +
        (c.seeded ? '<h4>Your text against the file</h4><div data-addiff>' + bodyDiff(p.sourceText, d.body) + '</div>'
          : '<p class="b-note">This file is not converted to text: write the explanation yourself.</p>');
    }
  }
  const ready = ad.op === 'refresh' ? d.reason.trim().length >= 3
    : d.title.trim() && (ad.op === 'catalogue' || d.body.trim());
  const apply = ad.confirm
    ? `<p class="b-warn" role="status">This writes one file, <code>brain/${esc(c.destination.id)}.md</code>, ` +
      'and commits it where this board commits. Nothing else changes.</p>' +
      button('ad-apply', ad.busy ? 'Applying…' : 'Confirm: apply', { primary: true, disabled: ad.busy }) +
      button('ad-unconfirm', 'Not yet')
    : button('ad-confirm', 'Apply…', { primary: true, disabled: !ready });
  return `<div class="b-form b-adform">${form}` +
    (ad.applyError ? `<p class="b-warn" role="alert" tabindex="-1">${esc(ad.applyError)}</p>` : '') +
    (ad.lost ? '<p class="b-warn" role="alert">No answer reached this page. Apply again: the same request ' +
      'cannot write twice.</p>' : '') +
    `<div class="b-adactions">${apply}</div></div>`;
}

function adoptHtml() {
  if (!ad) {
    // no control for nothing: a board with no reference files is not asked
    if (!(cache.index && cache.index.data && cache.index.data.referenceFiles)) return '';
    return '<section class="b-adopt" aria-labelledby="ad-h"><h2 id="ad-h">Existing reference material</h2>' +
      '<p class="b-note"><code>reference/</code> is kept for what is already there; new knowledge belongs in the ' +
      `Brain. Adopting is one file at a time and leaves every original at its address.</p>${button('ad-open', 'Review reference files')}</section>`;
  }
  const inv = ad.inventory;
  let inner;
  if (ad.name) inner = adPreviewHtml();
  else if (ad.listError) inner = `<p class="b-warn" role="alert">${esc(ad.listError)}</p>`;
  else if (!inv) inner = '<p class="b-loading" role="status" aria-busy="true">Reading reference/…</p>';
  else if (inv.directory.state === 'refused') inner = `<p class="b-warn" role="alert">${esc(inv.directory.why)}</p>`;
  else if (!inv.items.length) inner = '<p class="b-none">There is nothing in <code>reference/</code> to adopt.</p>';
  else {
    inner = `<ul class="b-adlist">${inv.items.map(adItemHtml).join('')}</ul>` +
      (inv.truncated ? `<p class="b-warn">Only the first ${inv.limits.files} files are listed.</p>` : '') +
      (inv.linksTruncated ? '<p class="b-note">Inbound links were not all read; a count may be low.</p>' : '');
  }
  return '<section class="b-adopt b-panel" aria-labelledby="ad-h"><div class="b-headrow">' +
    `<h2 id="ad-h">Existing reference material</h2>${button('ad-close', 'Close')}</div>` +
    '<p class="b-note">Reading this list writes nothing, fetches nothing and runs nothing. Adopting grants no ' +
    `agent access and no authority.</p>${writeStatus(ADOPT_OP)}${inner}</section>`;
}

async function adApply() {
  if (!ad || ad.busy || !ad.preview) return;
  const mine = ad;
  const p = mine.preview;
  const c = p.choices.find(x => x.op === mine.op);
  if (!c) return;
  mine.busy = true;
  mine.applyError = null;
  mine.lost = false;
  renderBrain(true);
  const d = mine.draft;
  const body = { request: mine.request, op: mine.op, name: p.name, source: p.sha256,
                 expected: c.destination.revision };
  if (mine.op === 'refresh') body.reason = d.reason;
  else {
    body.title = d.title;
    body.summary = d.summary;
    if (mine.op === 'explanation') { body.kind = d.kind; body.body = d.body; }
  }
  const res = await post('/api/brain/adoption/apply', body);
  mine.busy = false;
  if (ad !== mine) return;
  const landed = (op) => {
    ops[ADOPT_OP] = { request: mine.request, op, at: Date.now() };
    forget();
    mine.confirm = false;
    mine.name = null;
    mine.preview = null;
    mine.draft = null;
    mine.op = null;
    adLoad();
  };
  if (res.ok) {
    const op = res.data.operation;
    landed(op);
    toast(op.duplicate ? 'Already adopted — answered from the record.'
      : `Saved: ${op.what}` + (op.commit && op.commit.state === 'failed' ? ' — the commit failed' : ''),
      !!(op.commit && op.commit.state === 'failed'));
    renderBrain(true);
    $('#bshell .b-adopt .b-status')?.setAttribute('tabindex', '-1');
    $('#bshell .b-adopt .b-status')?.focus();
    return;
  }
  if (res.status === 0) {
    // no answer: the record says whether it landed; the same request is kept
    mine.lost = true;
    try {
      const r = await fetch('/api/brain/operation?request=' + encodeURIComponent(mine.request));
      const data = await r.json().catch(() => ({}));
      if (data.operation && data.operation.saved) {
        landed(data.operation);
        mine.lost = false;
        toast('The adoption had landed — the board\'s record says so.');
      }
    } catch (e) { /* the warning stands */ }
    renderBrain(true);
    return;
  }
  // refused: nothing was written. What it was held to is read again
  mine.request = newRequest();
  mine.confirm = false;
  mine.applyError = res.error;
  renderBrain(true);
  if (res.data && res.data.stale) adPreview(mine.name);
  $('#bshell .b-adform [role="alert"]')?.focus();
}

function adAct(name, arg) {
  if (name === 'ad-open') {
    ad = { inventory: null, name: null, preview: null, op: null, draft: null, request: newRequest(),
           busy: false, confirm: false };
    adLoad();
    renderBrain(true);
    $('#bshell [data-bact="ad-close"]')?.focus();
    return;
  }
  if (!ad) return;
  if (name === 'ad-close') {
    ad = null;
    delete ops[ADOPT_OP];
    renderBrain(true);
    $('#bshell [data-bact="ad-open"]')?.focus();
    return;
  }
  if (name === 'ad-pick') {
    ad.name = arg;
    ad.preview = null;
    ad.previewError = null;
    ad.applyError = null;
    ad.op = null;
    ad.draft = null;
    ad.confirm = false;
    adPreview(arg);
    renderBrain(true);
    $('#bshell [data-bact="ad-back"]')?.focus();
    return;
  }
  if (name === 'ad-back') {
    const was = ad.name;
    ad.name = null;
    ad.preview = null;
    ad.draft = null;
    ad.confirm = false;
    ad.applyError = null;
    adLoad();
    renderBrain(true);
    $(`#bshell [data-bact="ad-pick"][data-barg="${CSS.escape(was || '')}"]`)?.focus();
    return;
  }
  if (name === 'ad-confirm') {
    ad.confirm = true;
    renderBrain(true);
    $('#bshell [data-bact="ad-apply"]')?.focus();
    return;
  }
  if (name === 'ad-unconfirm') {
    ad.confirm = false;
    renderBrain(true);
    $('#bshell [data-bact="ad-confirm"]')?.focus();
    return;
  }
  if (name === 'ad-apply') adApply();
}

/* An adopted entry's relationship to the file it came from, read now. */
function adoptionHtml(e) {
  const a = e.adoption;
  if (!a) return '';
  const word = { unchanged: 'Unchanged since adopted', changed: 'Changed since adopted',
                 missing: 'The file is gone', unreadable: 'Cannot be read' }[a.state] || a.state;
  return '<div class="b-panel b-adoption"><h2>Adopted from</h2><dl class="b-facts">' +
    `<dt>File</dt><dd><a href="${esc(a.address)}" target="_blank" rel="noopener noreferrer">reference/${esc(a.name)}</a></dd>` +
    `<dt>Role</dt><dd>${a.role === 'catalogue' ? 'Catalogues the retained file' : 'Maintained explanation of it'}</dd>` +
    `<dt>Source now</dt><dd>${esc(word)}</dd>` +
    `<dt>Recorded</dt><dd class="mono b-adhash">${esc(a.recorded)}</dd>` +
    (a.counterpart ? `<dt>${a.counterpart.role === 'catalogue' ? 'Retained source' : 'Explanation'}</dt>` +
      `<dd>${entryLink(a.counterpart)} ${classChip(a.counterpart.classification)}</dd>` : '') +
    '</dl><p class="b-note">Adoption records where this came from. It is not a claim that the content is correct.</p></div>';
}

async function hbSave() {
  if (!hb || hb.busy) return;
  hb.busy = true;
  hb.error = null;
  hb.lostReply = false;
  renderBrain(true);
  const mine = hb;
  const res = await post('/api/brain/handbook', { request: mine.request, expected: mine.base,
    section: mine.section, entries: mine.draft, reason: mine.reason });
  mine.busy = false;
  if (hb !== mine) return;
  if (res.ok) {
    const op = res.data.operation;
    ops[HANDBOOK_OP] = { request: mine.request, op, at: Date.now() };
    hb = null;
    forget();
    toast(op.duplicate ? 'Already saved — this request was answered from the record.'
      : `Saved: ${op.what}` + (op.commit && op.commit.state === 'failed' ? ' — the commit failed' : ''),
      !!(op.commit && op.commit.state === 'failed'));
    renderBrain(true);
    $(`#bshell [data-bact="hb-organize"][data-barg="${CSS.escape(mine.section)}"]`)?.focus();
    return;
  }
  if (res.status === 0) {
    mine.lostReply = true;
    try {
      const r = await fetch('/api/brain/operation?request=' + encodeURIComponent(mine.request));
      const data = await r.json().catch(() => ({}));
      if (data.operation && data.operation.saved) {
        ops[HANDBOOK_OP] = { request: mine.request, op: data.operation, at: Date.now() };
        hb = null;
        forget();
        toast('The save had landed — the board\'s record says so.');
      }
    } catch (e) { /* the warning stands */ }
    renderBrain(true);
    return;
  }
  mine.request = newRequest();
  if (res.data && res.data.stale) mine.conflict = { error: res.error, current: res.data.current };
  else mine.error = res.error;
  renderBrain(true);
  $('#bshell .b-hbedit .b-conflict, #bshell .b-hbedit [role="alert"]')?.focus();
}

function hbAct(name, arg) {
  const h = cache.handbook && cache.handbook.data;
  if (name === 'hb-organize' && h) {
    const s = h.sections.find(x => x.id === arg);
    if (!s) return;
    hb = { section: s.id, draft: s.items.map(i => i.id), base: h.record.revision, reason: '', add: '',
           request: newRequest(), busy: false, error: null, conflict: null, lostReply: false };
    if (!cache.index) load('index');
    renderBrain(true);
    $('#bshell .b-hbedit select, #bshell .b-hbedit input')?.focus();
    return;
  }
  if (!hb) return;
  const k = Number(arg);
  if (name === 'hb-save') { hbSave(); return; }
  if (name === 'hb-cancel') {
    const was = hb.section;
    hb = null;
    renderBrain(true);
    $(`#bshell [data-bact="hb-organize"][data-barg="${CSS.escape(was)}"]`)?.focus();
    return;
  }
  if (name === 'hb-up' && k > 0) [hb.draft[k - 1], hb.draft[k]] = [hb.draft[k], hb.draft[k - 1]];
  else if (name === 'hb-down' && k < hb.draft.length - 1) [hb.draft[k + 1], hb.draft[k]] = [hb.draft[k], hb.draft[k + 1]];
  else if (name === 'hb-remove') hb.draft.splice(k, 1);
  else if (name === 'hb-add' && hb.add && !hb.draft.includes(hb.add)) { hb.draft.push(hb.add); hb.add = ''; }
  else if (name === 'hb-rebase' && hb.conflict) {
    // the current version becomes the base; the person's order stays
    const cur = hb.conflict.current;
    hb.base = cur && cur.record ? cur.record.revision : hb.base;
    hb.conflict = null;
    if (cur) cache.handbook = { status: 200, data: cur, error: null, at: Date.now() };
  }
  renderBrain(true);
  // focus follows the item that moved, or the control in its place
  const to = name === 'hb-up' ? k - 1 : name === 'hb-down' ? k + 1 : k;
  const again = $(`#bshell [data-bact="${CSS.escape(name)}"][data-barg="${to}"]:not([disabled])`) ||
    $(`#bshell .b-hborg:nth-child(${Math.max(1, Math.min(to + 1, hb ? hb.draft.length : 1))}) button:not([disabled])`) ||
    $('#bshell #hb-add');
  again?.focus();
}

/* ── writing ───────────────────────────────────────────────────────── */

function draftOf(mode, e) {
  if (mode === 'create') {
    return { title: '', kind: 'Guide', summary: '', sources: '', related: '', body: '',
             classification: 'Unclassified', reason: '',
             standing: '', policy: '', policyNote: '', bindings: '', assessed: '' };
  }
  if (mode === 'edit') {
    return { title: e.title || '', kind: e.entryKind || 'Guide', summary: e.summary || '',
             sources: (e.sources || []).map(s => s.text).join('\n'),
             related: (e.related || []).map(r => r.id).join(', '), body: e.body || '',
             standing: e.standing || '', policy: e.policy || '', policyNote: e.policyNote || '',
             bindings: (e.bindings || []).map(b => b.text).join('\n'), assessed: e.assessed || '' };
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

/* What the entry stands for and what backs it (task 171): a person's
   record, never inferred — a fold, so the ordinary edit stays short. */
function authorityFields(d) {
  const opts = (name, value, pairs) => `<select id="bf-${name}" data-bf="${name}">` + pairs.map(([v, l]) =>
    `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(l)}</option>`).join('') + '</select>';
  const open = d.standing || d.policy || d.bindings || d.assessed;
  return `<details class="b-fieldset b-authfields"${open ? ' open' : ''}><summary>Source authority ` +
    '<span class="b-note">— what it stands for, and which revisions back it</span></summary>' +
    field('standing', 'Standing', opts('standing', d.standing, [['', 'Not stated'],
      ['implemented', 'Implemented'], ['deployed', 'Deployed (record the deployment below)'],
      ['agreed-future', 'Agreed future work, not built'], ['unresolved', 'Unresolved']]),
      'Current is not shipped: say what this explains. Bench never infers it.') +
    field('policy', 'Working policy', opts('policy', d.policy, [['', 'Not a working agreement'],
      ['approved', 'Approved policy'], ['inferred', 'Inferred convention — not approved']]),
      'Approved only if people actually approved it; say by whom and where below.') +
    field('policyNote', 'Policy note', input('policyNote', d.policyNote, { spell: true })) +
    field('bindings', 'Source bindings', textarea('bindings', d.bindings, 3,
      'One per line: app:&lt;path&gt;@&lt;commit&gt;, work:&lt;path&gt;@&lt;commit&gt;, ' +
      'review:#&lt;card&gt;/&lt;review&gt;, deploy:&lt;where&gt;@&lt;identity&gt;.'),
      'One per line: app:&lt;path&gt;@&lt;commit&gt;, work:&lt;path&gt;@&lt;commit&gt;, ' +
      'review:#&lt;card&gt;/&lt;review&gt;, deploy:&lt;where&gt;@&lt;identity&gt;. A deployment identity is ' +
      'your record; bench does not verify it.') +
    field('assessed', 'Assessed on', input('assessed', d.assessed), 'The date you checked these bindings, YYYY-MM-DD.') +
    '</details>';
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
      authorityFields(d) +
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
    `<dt>Classification</dt><dd>${classChip(e.classification)} <span class="b-note">${esc(ABOUT[e.classification] || '')}</span>` +
    (DEFAULT_SHOW[e.classification] === false
      ? `<p class="b-note b-hiddenby">Hidden from the Brain's default list; its address always opens it, ` +
        `and <strong>Show classifications → ${esc(e.classification)}</strong> lists it.</p>` : '') + '</dd>' +
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
    `<aside class="b-side" aria-label="About this entry"><div class="b-panel">${facts}</div>${adoptionHtml(e)}${authorityHtml(e)}` +
    `<div class="b-panel"><h2>Classification history</h2>${history}</div>${revisionsHtml(e.id)}</aside></div>` +
    warnings + '</article>';
}

/* ── a review filed on a card (task 168) ──────────────────────────────

   The page reads `GET /api/brain/review`: the card's stored report — the
   very text and digest the card's Reviews section shows, with any
   clipping notice — what bench recorded when it filed it, whether that
   still holds, and the review's classification. Nothing on this page
   edits the report: Classify and the manual override write one record
   under brain/reviews/, through the Brain's one writer, held to the
   record this page read. */

let red = null;   // the review classification in progress: {key, mode, draft, busy, error, request}

const ASSESS = { current: 'Current', stale: 'Stale assessment', unknown: 'Unknown' };
const unknownWord = (why) => `<span class="b-none">unknown — ${esc(why)}</span>`;

function refHtml(ref, what) {
  if (!ref || !(ref.commit || ref.ref)) return unknownWord(`the ${what} was not recorded when this review was filed`);
  return (ref.commit ? `<span class="mono b-sha">${esc(ref.commit)}</span>` : '') +
    (ref.prefix ? ' <span class="b-note">(the 12 characters its heading names)</span>' : '') +
    (ref.ref ? ` <span class="b-note">on</span> <span class="mono">${esc(ref.ref)}</span>` : '') +
    `<span class="b-note"> · said by ${esc(ref.authority || 'nobody recorded')}</span>`;
}

function reviewFacts(v) {
  const p = v.provenance;
  const r = v.report;
  const rows = [];
  rows.push(`<dt>Card</dt><dd><a href="${esc(v.source.address)}" data-route>${v.source.number
    ? '#' + esc(v.source.number) : esc(v.source.file)} — ${esc(v.source.title)}</a>` +
    `<span class="b-note"> · ${v.source.archived ? 'archived' : esc(v.source.stage)}</span></dd>`);
  rows.push(`<dt>Kind</dt><dd>${esc(r.kindWord)}${r.rounds > 1 ? ` · ${r.round} of ${r.rounds} on this card` : ''}</dd>`);
  rows.push(`<dt>Reviewer</dt><dd>${esc(r.reviewer || p.reviewer || 'not recorded')}</dd>`);
  rows.push(`<dt>Model</dt><dd>${p.model ? `<span class="mono">${esc(p.model)}</span>` : '<span class="b-none">not recorded</span>'}</dd>`);
  rows.push(`<dt>Verdict</dt><dd>${esc(r.verdict || r.verdictLine || 'no verdict line')}` +
    (r.type === 'pr' ? '<p class="b-note">Bench\'s review verdict, as filed. It is not a formal GitHub approval, ' +
      'and the merge rules read it on their own terms.</p>' : '') + '</dd>');
  rows.push(`<dt>Filed</dt><dd>${esc(r.at)}${p.filed ? ` <span class="b-note mono">(${esc(p.filed)})</span>` : ''}` +
    (r.legacy ? '<p class="b-note">Filed in an older format; nothing more was recorded.</p>' : '') + '</dd>');
  if (r.type === 'relevance') {
    rows.push(`<dt>Brief assessed</dt><dd>${p.briefRevision
      ? `<span class="mono b-sha">${esc(p.briefRevision.slice(0, 12))}</span> <span class="b-note">— the revision the run was launched with</span>`
      : unknownWord('the revision this review read was not recorded')}` +
      `<p class="b-note">The brief now: <span class="mono">${esc(v.source.brief)}</span></p></dd>`);
  } else {
    rows.push(`<dt>Head assessed</dt><dd>${refHtml(p.head, 'head')}</dd>`);
    rows.push(`<dt>Base</dt><dd>${refHtml(p.base, 'base')}</dd>`);
    rows.push(`<dt>Pull request</dt><dd>${p.pr ? `<a href="${esc(p.pr)}" target="_blank" rel="noopener noreferrer">` +
      `${p.prNumber ? '#' + esc(p.prNumber) : esc(p.pr)}</a>${p.prState ? ` <span class="b-note">${esc(p.prState.toLowerCase())} when reviewed</span>` : ''}`
      : '<span class="b-none">not recorded</span>'}` +
      (p.label ? `<p class="b-note">Filed as ${esc(p.label)}${p.note ? ` — ${esc(p.note)}` : ''}</p>` : '') +
      (p.published ? `<p class="b-note">${esc(p.published)}</p>` : '') + '</dd>');
  }
  rows.push(`<dt>Report digest</dt><dd><span class="mono">${esc(r.reportSha)}</span>` +
    `<span class="b-note"> · ${p.digestAgrees === true ? 'the same text bench recorded filing'
      : p.digestAgrees === false ? 'differs from what bench recorded filing — the card was edited since'
        : 'no digest was recorded at filing'}</span></dd>`);
  rows.push(`<dt>Evidence</dt><dd>${p.evidence.length ? '<ul class="b-links">' + p.evidence.map(x => x.found
    ? `<li><span class="mono">${esc(x.id)}</span> — ${esc(x.check || '')} ${esc(x.outcome || '')}` +
      `${x.commit ? ` at <span class="mono">${esc(x.commit)}</span>` : ''}</li>`
    : `<li><span class="mono">${esc(x.id)}</span> <span class="b-missing">not on this board</span> ` +
      '<span class="b-note">— evidence is machine-local to the board that ran it</span></li>').join('') + '</ul>'
    : '<span class="b-none">none recorded</span>'}</dd>`);
  if (p.localRecord) {
    rows.push(`<dt>Review checkout record</dt><dd>${p.localRecord === 'present' ? 'on this board'
      : '<span class="b-missing">unavailable on this board</span> <span class="b-note">— machine-local; ' +
        'the provenance above travelled with the card</span>'}</dd>`);
  }
  rows.push(`<dt>Identity</dt><dd class="mono">${esc(r.id)}</dd>`);
  return `<dl class="b-facts">${rows.join('')}</dl>`;
}

function reviewClassHtml(v) {
  const c = v.classification;
  const history = c.history.length
    ? '<ol class="b-history">' + c.history.slice().reverse().map(h =>
        `<li><span class="mono">${esc(h.at)}</span> ${classChip(h.classification)} ` +
        `<span>${esc(h.actor)}</span> — ${esc(h.reason)}</li>`).join('') + '</ol>'
    : c.source === 'closed-ticket'
      ? '<p class="b-none" data-closed-default>Nobody has classified this review. It reads Historical because ' +
        'its card is closed — in Done or the archive. That is a lifecycle default, not a person\'s decision or ' +
        'an agent\'s assessment, and it does not mean the review failed, was superseded or that its code was ' +
        'merged or deployed. Nothing was written to say so; reopening the card returns it to Unclassified, and ' +
        'a classification you record here takes precedence.</p>'
      : '<p class="b-none">Nothing has been recorded about this review yet: it reads Unclassified, ' +
        'and nothing was written to say so.</p>';
  return `<div class="b-panel"><h2>Classification</h2><p>${classChip(c.classification)} ` +
    (c.source === 'closed-ticket' ? chip('b-note', 'from the closed ticket') + ' ' : '') +
    `<span class="b-note">${esc(ABOUT[c.classification] || '')}</span></p>` +
    (c.override ? `<p class="b-note">Manual override: ${esc(c.override)}</p>` : '') +
    (c.supersededBy ? `<p class="b-note">Superseded by <a href="${esc(entryHref(c.supersededBy))}" data-route>${esc(c.supersededBy)}</a></p>` : '') +
    (c.invalidated ? `<p class="b-note">Invalidated: ${esc(c.invalidated)}</p>` : '') +
    history + (c.file ? `<p class="b-note mono">brain/${esc(c.file)}</p>` : '') + '</div>';
}

function reviewFormHtml(v) {
  const d = red.draft;
  const sel = (name, value, options) => `<select data-rvf="${name}">` + options.map(([val, label]) =>
    `<option value="${esc(val)}"${val === value ? ' selected' : ''}>${esc(label)}</option>`).join('') + '</select>';
  const entriesHere = ((cache.index && cache.index.data && cache.index.data.entries) || []).filter(e => !e.tombstone);
  let fields = '';
  if (red.mode === 'classify') {
    fields += `<label class="b-field"><span>Classification</span>${sel('classification', d.classification,
      CLASSES.map(c => [c, c]))}</label>`;
    if (d.classification === 'Superseded') {
      fields += `<label class="b-field"><span>Replaced by entry</span>${sel('supersededBy', d.supersededBy || '',
        [['', 'none — invalidated with no replacement']].concat(entriesHere.map(e => [e.id, `${e.title} (${e.id})`])))}</label>`;
      if (!d.supersededBy) {
        fields += `<label class="b-field"><span>Why it no longer holds</span><input type="text" data-rvf="invalidation" ` +
          `value="${esc(d.invalidation || '')}" maxlength="500"></label>`;
      }
    }
  }
  fields += `<label class="b-field"><span>Reason</span><input type="text" data-rvf="reason" value="${esc(d.reason || '')}" ` +
    'maxlength="500" required></label>';
  const title = red.mode === 'classify' ? 'Classify this review'
    : (v.classification.override ? 'Clear the manual override' : 'Set a manual override');
  return `<section class="b-panel b-form" aria-label="${esc(title)}"><h2>${esc(title)}</h2>` +
    '<p class="b-note">Writes one record under <code>brain/reviews/</code>, attributed to this board\'s identity. ' +
    'The report on the card is not changed.</p>' + fields +
    (red.error ? `<p class="b-warn" role="alert">Not saved: ${esc(red.error)}</p>` : '') +
    '<div class="b-actions">' + button('rv-save', red.busy ? 'Saving…' : 'Save', { primary: true, busy: red.busy, disabled: red.busy }) +
    button('rv-cancel', 'Cancel', { disabled: red.busy }) + '</div></section>';
}

function reviewHtml(slot, k) {
  const v = slot.data;
  const [cardRaw, rid] = k.slice(2).split('/');
  const backLink = '<p class="b-back"><a href="/brain" data-route>All Brain entries</a></p>';
  if (!v) {
    return backLink + `<div class="b-panel b-bad" role="alert"><h1 class="b-title" tabindex="-1">Review ` +
      `${esc(rid)}</h1><p>${esc(slot.error || 'the review could not be read')}</p></div>`;
  }
  const recorded = v.classification
    ? `<p>A classification is still recorded for it (${classChip(v.classification.classification)}, ` +
      `<code>brain/${esc(v.classification.file)}</code>): its source is missing, and the record is kept.</p>` : '';
  if (v.state === 'no-card') {
    return backLink + `<div class="b-panel" role="alert"><h1 class="b-title" tabindex="-1">No card ${esc(cardRaw)} ` +
      'on this board</h1><p>No card in a stage or in the archive carries this number, so the review ' +
      `<span class="mono">${esc(rid)}</span> cannot be read here. It may belong to another board.</p>${recorded}</div>`;
  }
  if (v.state === 'no-review') {
    return backLink + `<div class="b-panel" role="alert"><h1 class="b-title" tabindex="-1">No review ${esc(rid)} ` +
      `on card ${esc(cardRaw)}</h1><p>The card is here (${v.files.map(f => `<code>${esc(f)}</code>`).join(', ')}), ` +
      'and none of the reviews filed on it has this identity. No other review is shown in its place.</p>' +
      `${recorded}<p><a href="/cards/${esc(encodeURIComponent(cardRaw))}?tab=reviews" data-route>The card's reviews</a></p></div>`;
  }
  if (v.state === 'ambiguous') {
    return backLink + `<div class="b-panel b-bad" role="alert"><h1 class="b-title" tabindex="-1">${v.files.length} cards ` +
      `numbered ${esc(cardRaw)} hold review ${esc(rid)}</h1><p>The board does not pick one. Each card:</p><ul>` +
      v.files.map(f => `<li><code>${esc(f)}</code></li>`).join('') + '</ul></div>';
  }
  const r = v.report;
  const a = v.assessment;
  const shared = v.source.sharedNumber.length
    ? `<p class="b-note">Another card here shares the number #${esc(v.source.number)} ` +
      `(${v.source.sharedNumber.map(f => `<code>${esc(f)}</code>`).join(', ')}); this review is on ` +
      `<code>${esc(v.source.stage)}/${esc(v.source.file)}</code> alone.</p>` : '';
  const actions = red && red.key === k ? reviewFormHtml(v)
    : '<div class="b-actions b-entryactions" role="group" aria-label="Classify this review">' +
      button('rv-classify', 'Classify') +
      button('rv-override', v.classification.override ? 'Clear override' : 'Set override') + '</div>';
  const others = v.others.length ? `<div class="b-panel"><h2>Other ${esc(r.kindWord.toLowerCase())}s on this card</h2>` +
    '<ul class="b-links">' + v.others.map(o => `<li><a href="${esc(o.address)}" data-route>${esc(o.at)}</a> ` +
      `<span class="b-note">${esc(o.verdict || '')}</span></li>`).join('') + '</ul></div>' : '';
  const linked = `<div class="b-panel"><h2>Cited by</h2>${v.linkedFrom.length ? '<ul class="b-links">' +
    v.linkedFrom.map(x => `<li>${entryLink(x)} <span class="b-note">— cites ${x.via === 'review' ? 'this review' : 'its card'}</span></li>`).join('') + '</ul>'
    : '<p class="b-none">No Brain entry names this review or its card as a source.</p>'}</div>`;
  const stale = slot.error ? `<p class="b-warn" role="status">${esc(slot.error)} — showing the last reading.</p>` : '';
  return backLink + stale +
    `<article class="b-entry b-review" aria-labelledby="btitle"><header class="b-head">` +
    `<p class="b-kicker">${chip('b-kind', 'Review')} ${chip('b-kind', r.kindWord)} ${classChip(v.classification.classification)}` +
    `${v.classification.override ? chip('b-override', 'Manual override') : ''}${v.source.archived ? chip('b-c-removed', 'Card archived') : ''}</p>` +
    `<h1 class="b-title" id="btitle" tabindex="-1">${esc(r.title)}</h1>` +
    `<p class="b-lede">${esc(r.kindWord)} filed on its card by ${esc(r.reviewer || 'a reviewer not recorded')} on ${esc(r.at)}. ` +
    'The report is the card\'s: shown here as filed, never copied or edited.</p>' +
    `<p class="b-permalink"><span class="mono">${esc(v.address)}</span> ` +
    `<button type="button" data-copy-link="${esc(v.address)}">Copy link</button> ` +
    `<a class="b-oncard" href="${esc(v.source.reviewsAddress)}" data-route>Open on the card</a></p></header>` +
    shared +
    `<p class="b-assess b-assess-${esc(a.state)}" role="note"><strong>${esc(ASSESS[a.state] || a.state)}</strong> — ${esc(a.why)}</p>` +
    (r.clipped ? `<p class="b-warn" role="note">This report was clipped when it was filed: “${esc(r.clipped)}”</p>` : '') +
    actions +
    `<div class="b-read"><div class="b-prose">${md(r.body, { base: 2 }) || '<p class="b-none">The report is empty.</p>'}</div>` +
    `<aside class="b-side" aria-label="About this review"><div class="b-panel"><h2>Provenance</h2>${reviewFacts(v)}</div>` +
    reviewClassHtml(v) + linked + others + '</aside></div></article>';
}

async function saveReview() {
  const slot = cache.entries[key()];
  const v = slot && slot.data;
  if (!red || red.busy || !v || v.state !== 'found') return;
  const d = red.draft;
  const body = { request: red.request, card: v.cardId, review: v.review,
                 expected: v.classification.revision || null, reason: d.reason || '' };
  let path;
  if (red.mode === 'classify') {
    path = '/api/brain/review/classify';
    body.classification = d.classification;
    if (d.classification === 'Superseded') {
      if (d.supersededBy) body.supersededBy = d.supersededBy;
      else body.invalidation = d.invalidation || '';
    }
  } else {
    path = '/api/brain/review/override';
    body.action = v.classification.override ? 'clear' : 'set';
  }
  red.busy = true;
  red.error = null;
  renderBrain(true);
  const res = await post(path, body);
  if (!red) return;
  red.busy = false;
  if (res.ok) {
    toast(red.mode === 'classify' ? `Classified ${d.classification}.` : 'Override saved.');
    red = null;
    forget();
    renderBrain(true);
    $('#bshell .b-title')?.focus();
    return;
  }
  red.error = res.status ? res.error || 'the board refused it'
    : 'no answer came back, and nothing is known to have been saved; Save again sends the same request';
  // any answer is final for this request: a new attempt is a new one. No
  // answer keeps it, so a resend is answered from the board's record
  if (res.status) red.request = newRequest();
  if (res.data && res.data.stale) { forget(); }
  renderBrain(true);
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
    // a request this board does not have (a tab kept it from another board,
    // or it expired) is gone for good: the poll stops asking for it
    drafts.one[id] = { data: data.draft || null, error: data.error || null, gone: res.status === 404, at: Date.now() };
  } catch (e) {
    drafts.one[id] = { ...(drafts.one[id] || {}), error: 'the board could not be asked', at: Date.now() };
  }
  inflight.delete(k);
  const d = drafts.one[id].data;
  // every conversation about this request starts its edit from the draft
  if (d) for (const s of slotsOf('brain')) if (s.data.draftId === id) seedEdit(d, s.data);
  renderBrain();
}

async function loadPreflight(a = ag) {
  if (!a || a.pfLoading) return;
  a.pfLoading = true;
  const q = new URLSearchParams();
  if (a.target) q.set('target', a.target);
  if (a.text) q.set('text', a.text.slice(0, 2000));
  try {
    const res = await fetch('/api/brain/draft/preflight?' + q.toString());
    const data = await res.json().catch(() => ({}));
    a.preflight = data.preflight || { error: data.error || 'the board could not say' };
  } catch (e) {
    a.preflight = { error: 'the board could not be asked' };
  }
  a.pfLoading = false;
  // a reading arriving, not a person's act: it never replaces the button
  // under a pressed pointer (the release repaints), and it paints under
  // the person's typing with their caret kept
  renderBrain('live');
}

/* The Brain Agent's conversations live in the shared panel (task 205): one
   per target — a new entry, an entry to update, or a request opened from
   the list — each kept with its unsent text while the person reads other
   entries or leaves the Brain. `ag` is the one the panel shows. */
function composerAg(target, revision, title) {
  return { mode: 'compose', target: target || null, expected: revision || null, targetTitle: title || null,
           text: '', request: agRequest(), preflight: null, busy: false, error: null };
}

function draftAg(id) {
  return { mode: 'draft', draftId: id, answers: {}, note: '', edit: null, view: 'rendered',
           busy: false, error: null, request: agRequest() };
}

function openComposer(target, revision, title, from) {
  const s = openAgent('brain', target ? { entry: target, title: title || null } : { entry: null },
                      { fresh: true, revision, title, from });
  // a resumed, unsent request is held to the entry as it reads now
  if (s && s.data.mode === 'compose' && target && revision && s.data.expected !== revision) {
    s.data.expected = revision;
    savePanel();
  }
}

function openDraft(id, from) {
  const have = slotsOf('brain').find(s => s.data.draftId === id);
  if (have) openAgent('brain', have.target, { from });
  else openAgent('brain', { draft: id }, { from });
}

function currentDraft(a = ag) {
  return a && a.draftId && drafts.one[a.draftId] ? drafts.one[a.draftId].data : null;
}

// a request in one of these is history: asking again starts a new one (a
// failed one stays listed under Brain Agent requests, to retry or discard)
const AG_DONE = ['applied', 'cancelled', 'discarded', 'failed'];

function registerBrainAgent() {
  registerAgent('brain', {
    label: 'Brain Agent',
    capability: 'Drafts one entry from the project; writes nothing until you apply it.',
    key: (t) => t.draft ? `brain:d:${t.draft}` : t.entry ? `brain:e:${t.entry}` : 'brain:new',
    create: (t, opts) => t.draft ? draftAg(t.draft) : composerAg(t.entry, opts.revision, t.title || opts.title),
    // a request that has ended is history: asking again starts a new one
    finished: (s) => {
      const d = currentDraft(s.data);
      return s.data.mode === 'draft' && !!d && AG_DONE.includes(d.status);
    },
    about: (s) => {
      const a = s.data;
      const d = currentDraft(a);
      const entry = (d && d.target) || a.target;
      if (entry) {
        const e = (cache.entries['e:' + entry] || {}).data;
        return `Updating ${(e && e.title) || a.targetTitle || entry}`;
      }
      return a.mode === 'draft' && !d ? `Request ${a.draftId}` : 'A new Brain entry';
    },
    body: (s) => { ag = s.data; return agentPanelHtml(); },
    opened: (s) => {
      ag = s.data;
      if (ag.mode === 'compose' && !ag.preflight) loadPreflight(ag);
      if (ag.draftId) loadDraft(ag.draftId);
    },
    persist: (s) => {
      const a = s.data;
      return { mode: a.mode, target: a.target || null, expected: a.expected || null, targetTitle: a.targetTitle || null,
               text: a.text || '', request: a.request, draftId: a.draftId || null, answers: a.answers || {},
               note: a.note || '', view: a.view || 'rendered', edit: a.edit || null };
    },
    revive: (d) => {
      if (d.mode !== 'compose' && d.mode !== 'draft') return null;
      if (d.mode === 'draft' && typeof d.draftId !== 'string') return null;
      return { ...(d.mode === 'draft' ? draftAg(String(d.draftId || '')) : composerAg(d.target, d.expected, d.targetTitle)),
               ...d, preflight: null, busy: false, error: null, pfLoading: false };
    },
    wire: wireAgentPanel,
  });
}

/* The person's edit starts as the proposal, once per proposal round; a
   repaint or a re-read never overwrites what they typed. */
function seedEdit(d, a = ag) {
  if (!a || d.status !== 'proposed' || !d.proposal) return;
  if (a.edit && a.edit.round === d.proposalRound) return;
  const p = d.proposal;
  a.edit = { round: d.proposalRound, title: p.title, kind: p.kind, summary: p.summary || '',
              body: p.body, sources: (p.sources || []).join('\n'), related: (p.related || []).join(', '),
              classification: p.classification || 'Unclassified',
              reason: p.classificationWhy || '' };
}

/* A line diff, bounded: the exact change against the entry this draft
   read. Equal runs are folded to their edges. */
function bodyDiff(before, after) {
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
    (ag.error ? `<p class="b-warn" role="alert">Not started: ${esc(ag.error)}</p>` : '') +
    '<div class="b-actions">' +
    button('ag-generate', ph === 'armed' ? 'Confirm: launch the Brain Agent' : ag.busy ? 'Starting…' : 'Generate',
      { primary: true, armed: ph === 'armed', busy: ag.busy, disabled: ag.busy || !ag.text.trim() || !!blocked }) +
    button('ag-close', 'Cancel', { disabled: ag.busy }) +
    (ph === 'armed' ? '<span class="b-note" role="status">Click again to launch one bounded, read-only run. It costs tokens.</span>' : '') +
    // what the run would read arrives after the composer opens: drawn
    // below the actions, so its arrival never moves Generate under a hand
    '</div>' + preflightHtml(pf) + related + '</section>';
}

function progressHtml(d) {
  const p = d.progress || {};
  const tool = p.toolActivity;
  // nothing reported is said as that, never as "nothing remaining" (task 205)
  if (!p.doing && !p.blocked && !(p.done || []).length && !(p.remaining || []).length) {
    return '<div class="b-progress" aria-live="polite"><p class="b-note">No progress reported for this request' +
      (tool ? ` · last tool activity ${tool.last ? esc(new Date(tool.last * 1000).toLocaleTimeString()) : 'none yet'}` +
        ' — activity, not progress' : '') + '.</p></div>';
  }
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
  // the conversation is the panel's primary content (task 205), not a fold
  return lines.length ? '<section class="b-transcript" aria-label="Conversation"><h3>Conversation</h3>' +
    '<ol class="b-history">' + lines.join('') + '</ol></section>' : '';
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
          bodyDiff(target.body, e.body) : '<p class="b-note" role="status">Reading the entry…</p>')
      : bodyDiff('', e.body);
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
  const kept = d.error && d.error.kept
    ? `<div class="b-panel b-bad" role="alert"><p><strong>The last round failed (${esc(d.error.code || 'failed')}).</strong> ` +
      `${esc(d.error.why || '')}</p>${d.error.problems ? '<ul>' + d.error.problems.map(x =>
        `<li>${esc(x)}</li>`).join('') + '</ul>' : ''}</div>` : '';
  const ap = phaseOf(armed.store, 'ag-apply');
  return kept + suggest + conflict + tabs + `<div class="b-panel b-agdraft">${pane}</div>` +
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
    if (d.status === 'proposed') {
      return `<p class="b-note">This request has used its ${esc(String(d.maxRounds))} rounds: apply, edit and apply, or discard it.</p>`;
    }
    return d.status === 'questions' || d.status === 'failed'
      ? `<p class="b-note">This request has used its ${esc(String(d.maxRounds))} rounds and has no draft to apply: discard it and start a new request.</p>` : '';
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
      `${esc((slot && slot.error) || 'Reading the request…')}</p></section>`;
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
  // the transcript leads: the request, then each turn, then where it stands
  return `<section class="b-panel b-agent${tone}" aria-labelledby="bag-h" data-agdraft="${esc(d.id)}">` +
    `<h2 id="bag-h" tabindex="-1">Brain Agent · ${esc(state)}</h2>` +
    `<p class="b-note">${d.mode === 'update' ? `Updating <span class="mono">${esc(d.target)}</span>` : 'A new entry'} · ` +
    `round ${esc(String(d.round))} of ${esc(String(d.maxRounds))} · <span class="mono">${esc(d.id)}</span></p>` +
    `<blockquote class="b-request">${esc(d.text)}</blockquote>` + conversationHtml(d) + observedHtml(d) + body +
    (ag.error ? `<p class="b-warn" role="alert">${esc(ag.error)}</p>` : '') +
    `<p class="b-note">${esc(d.local || '')}. Closing the panel stops nothing; the request is kept here and listed under Brain Agent requests.</p></section>`;
}

/* What bench itself observed of the request, apart from what the agent
   reported (task 205): the round's state as recorded, never a percentage */
function observedHtml(d) {
  const words = {
    running: 'a round is running — bench is watching its process',
    questions: 'the round ended with questions for you',
    proposed: 'the round ended with a draft',
    ready: 'waiting for you to continue',
    applying: 'writing the entry', applied: 'ended: applied', failed: 'ended: failed',
    cancelled: 'ended: cancelled', discarded: 'ended: discarded',
  };
  return `<p class="b-note ap-observed"><strong>Observed:</strong> ${esc(words[d.status] || d.status)}.</p>`;
}

function agentPanelHtml() {
  if (!ag) return '';
  const d = currentDraft();
  // the entry an update is against: read for its exact changes
  const target = d ? d.target : ag.target;
  if (target && !cache.entries['e:' + target]) load('e:' + target);
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

/* The panel slot `ag` belongs to, so a request's answer lands on the
   conversation it was sent from whichever one the panel shows by then. */
function slotOfAg(a) { return slotsOf('brain').find(s => s.data === a) || null; }

async function agAct(name, arg, from) {
  if (name === 'ag-new') { openComposer(null, null, null, from); return true; }
  if (name === 'ag-ask') {
    const e = currentEntry();
    if (e) openComposer(e.id, e.revision, e.title, from);
    return true;
  }
  if (name === 'ag-manual') {
    const s = slotOfAg(ag);
    if (s) dropSlot(s.key);
    startWrite('create', null);
    if (!cache.index) load('index');
    return true;
  }
  if (name === 'ag-close') {
    // Cancel on an unsent request: it is put away, and nothing was sent
    const s = slotOfAg(ag);
    armed.sweep(k => !k.startsWith('ag-'));
    if (s) dropSlot(s.key); else closePanel();
    return true;
  }
  if (name === 'ag-open') { openDraft(arg, from); return true; }
  if (name === 'ag-view' && ag) { ag.view = arg; savePanel(); return true; }
  if (name === 'ag-target' && ag) {
    const hit = ((ag.preflight || {}).related || []).find(r => r.id === arg);
    ag.target = arg; ag.targetTitle = hit ? hit.title : arg; ag.expected = null; ag.preflight = null;
    loadPreflight(ag);
    savePanel();
    return true;
  }
  const costly = { 'ag-generate': 1, 'ag-continue': 1, 'ag-apply': 1, 'ag-discard': 1, 'ag-cancel': 1, 'ag-retarget': 1 };
  if (!costly[name] || !ag) return false;
  const said = armed.click(name, undefined, () => renderBrain(true));
  if (said === 'armed') return true;
  if (said === 'busy') return true;
  armed.disarm(name);
  const mine = ag;
  const d = currentDraft(mine);
  let url, body;
  if (name === 'ag-generate') {
    url = '/api/brain/draft/start';
    body = { request: mine.request, text: mine.text, target: mine.target || undefined, expected: mine.expected || undefined };
  } else if (name === 'ag-continue' && d) {
    url = '/api/brain/draft/continue';
    body = { id: d.id, request: mine.request, round: d.round, answers: mine.answers, note: mine.note };
  } else if (name === 'ag-apply' && d && mine.edit) {
    const e = mine.edit;
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
  mine.busy = true; mine.error = null;
  // the identity is kept before it is sent: a reload resends the same one
  savePanel();
  renderBrain(true);
  let res = await post(url, body);
  if (res.status === 0 && (name === 'ag-generate' || name === 'ag-continue' || name === 'ag-apply')) {
    // no answer: the same request identity again is answered from the record
    res = await post(url, body);
  }
  mine.busy = false;
  if (!res.ok) {
    mine.error = res.error || 'refused';
    if (res.data && res.data.draft) drafts.one[res.data.draft.id] = { data: res.data.draft, at: Date.now() };
    if (mine.draftId) loadDraft(mine.draftId);
    renderBrain(true);
    return true;
  }
  mine.request = agRequest();
  const got = res.data.draft;
  if (got) drafts.one[got.id] = { data: got, at: Date.now() };
  if (name === 'ag-generate' && got) {
    // the composer becomes the request it started, in the same slot
    Object.assign(mine, draftAg(got.id), { target: got.target || mine.target || null, targetTitle: mine.targetTitle });
    seedEdit(got, mine);
  }
  if (name === 'ag-continue') { mine.answers = {}; mine.note = ''; }
  savePanel();
  if (name === 'ag-apply' && res.data.operation) {
    const op = res.data.operation;
    ops[op.id] = { request: ((got && got.applied) || {}).request || '', op, at: Date.now() };
    forget();
    toast(`Applied: ${op.id} ${op.what}` + (op.commit && op.commit.state === 'failed' ? ' — the commit failed' : ''),
      !!(op.commit && op.commit.state === 'failed'));
  }
  drafts.at = 0;
  renderBrain(true);
  // focus follows the answer only on the conversation still shown
  if (ag === mine) $('#agentpanel #bag-h')?.focus();
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
    // the source-authority fields go only from a draft that has them: a
    // draft seeded elsewhere (the Brain Agent's) keeps what the entry has
    if (d.standing !== undefined) {
      Object.assign(p, { standing: d.standing || '', policy: d.policy || '',
        policyNote: d.policy ? (d.policyNote || '') : '',
        bindings: (d.bindings || '').split('\n').map(s => s.trim()).filter(Boolean),
        assessed: (d.assessed || '').trim() });
    }
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

function act(name, arg, from) {
  if (name.startsWith('ag-')) {
    // the agent's own steps: what is synchronous (opening, switching a
    // view) has happened by the time this paints; a POST paints again
    // when its answer arrives
    agAct(name, arg, from);
    renderBrain(true);
    if (['ag-new', 'ag-ask', 'ag-target'].includes(name)) $('#agentpanel [data-ag="text"]')?.focus();
    if (name === 'ag-manual') $('#bshell .b-form input')?.focus();
    if (name === 'ag-close' && !document.querySelector('#agentpanel:not([hidden])')) $('#bshell .b-title')?.focus();
    return;
  }
  if (name.startsWith('rf-')) { rfAct(name, arg); return; }
  if (name.startsWith('hb-')) { hbAct(name, arg); return; }
  if (name.startsWith('ad-')) { adAct(name, arg); return; }
  if (name.startsWith('rv-')) {
    // a filed review's classification (task 168): a mode of its page
    const slot = cache.entries[key()];
    const v = slot && slot.data;
    if (name === 'rv-save') { saveReview(); return; }
    if (name === 'rv-cancel') red = null;
    else if (v && v.state === 'found') {
      red = { key: key(), mode: name === 'rv-classify' ? 'classify' : 'override', busy: false,
              error: null, request: newRequest(),
              draft: { classification: v.classification.classification, reason: '',
                       supersededBy: '', invalidation: '' } };
      if (!cache.index) load('index');
    }
    renderBrain(true);
    if (red) $('#bshell [data-rvf]')?.focus();
    else $('#bshell [data-bact="rv-classify"]')?.focus();
    return;
  }
  const e = currentEntry();
  if (name === 'clear-find') {
    resetFind();
    renderBrain(true);
    $('#bshell [data-bkind="all"]')?.focus();
    return;
  }
  if (name === 'toggle-nav') {
    navOpen = !navOpen;
    renderBrain(true);
    $('#bshell [data-bact="toggle-nav"]')?.focus();
    return;
  }
  if (name === 'show-hidden') {
    // shows every classification the current view hides — a view change,
    // never an entry's; Reset to default view is the way back
    for (const c of LIB_ORDER) find.show[c] = true;
    renderBrain(true);
    // focus lands on the first entry it brought into view
    $('#bshell .b-results [data-brain-id]')?.focus();
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
  setSeedRepaint((force) => renderBrain(force));
  wireSeed(el);
  setEvalRepaint((force) => renderBrain(force));
  wireEvaluate(el);
  setRecHooks((force) => renderBrain(force), openEvaluation);
  wireReconcile(el);
  el.addEventListener('click', (ev) => {
    const k = ev.target.closest && ev.target.closest('[data-bkind]');
    if (k && el.contains(k)) {
      // choosing a kind narrows the library; it reads and writes nothing
      find.kind = k.dataset.bkind;
      renderBrain(true);
      return;
    }
    const b = ev.target.closest && ev.target.closest('[data-bact]');
    if (!b || !el.contains(b) || b.disabled) return;
    act(b.dataset.bact, b.dataset.barg, b);
  });
  const onField = (ev) => {
    const f = ev.target;
    if (S.brainSeed && seedField(f, ev)) return;
    if (!S.brainSeed && evalField(f, ev)) return;
    if (f.dataset && f.dataset.bfind) {
      // the list follows the search as it is typed: a person's own act,
      // painted at once with the caret kept where it was
      if (find[f.dataset.bfind] === f.value) return;
      find[f.dataset.bfind] = f.value;
      renderBrain(true);
      return;
    }
    if (f.dataset && f.dataset.bshow) {
      // showing or hiding a classification changes the view, never an entry
      if (ev.type !== 'change' || find.show[f.dataset.bshow] === f.checked) return;
      find.show[f.dataset.bshow] = f.checked;
      renderBrain(true);
      return;
    }
    if (hb && f.dataset && (f.dataset.hb || f.dataset.hbrep)) {
      // organizing a handbook section: kept here, sent nothing until Save
      if (f.dataset.hb === 'reason') hb.reason = f.value;
      else if (f.dataset.hb === 'add') {
        hb.add = f.value;
        const add = el.querySelector('[data-bact="hb-add"]');
        if (add) add.disabled = hb.busy || !hb.add;
      } else if (f.dataset.hbrep && ev.type === 'change' && f.value) {
        // replacing a link keeps its place in the order
        const k = Number(f.dataset.hbrep);
        if (!hb.draft.includes(f.value)) hb.draft[k] = f.value;
        renderBrain(true);
        $(`#bshell [data-hbrep="${k}"]`)?.focus();
      }
      return;
    }
    if (ad && f.dataset && f.dataset.adop) {
      // choosing what adopting does: a view of the preview, nothing is sent
      if (ev.type !== 'change' || !f.checked) return;
      adChoose(f.dataset.adop, false);
      renderBrain(true);
      $(`#bshell [data-adop="${CSS.escape(f.dataset.adop)}"]`)?.focus();
      return;
    }
    if (ad && ad.draft && f.dataset && f.dataset.ad) {
      // kept here, sent nothing until Apply. An edit after the first press
      // is a different entry from the one confirmed, so it asks again
      ad.draft[f.dataset.ad] = f.value;
      if (ad.confirm) { ad.confirm = false; renderBrain(true); return; }
      const c = ad.preview.choices.find(x => x.op === ad.op);
      const box = el.querySelector('[data-addiff]');
      // compared when the field is left: the comparison is too heavy for a keystroke
      if (ev.type === 'change' && box && f.dataset.ad === 'body' && c && c.seeded) box.innerHTML = bodyDiff(ad.preview.sourceText, f.value);
      const go = el.querySelector('[data-bact="ad-confirm"]');
      if (go) {
        const d = ad.draft;
        go.disabled = !(ad.op === 'refresh' ? d.reason.trim().length >= 3
          : d.title.trim() && (ad.op === 'catalogue' || d.body.trim()));
      }
      return;
    }
    if (f.dataset && f.dataset.rfreason) {
      // why a report is left unfiled: kept here, sent only with Close
      rfReasons[f.dataset.rfreason] = f.value;
      return;
    }
    if (red && f.dataset && f.dataset.rvf) {
      // kept here, sent nothing until Save; a choice that changes which
      // fields exist is drawn at once
      red.draft[f.dataset.rvf] = f.value;
      if (ev.type === 'change' && ['classification', 'supersededBy'].includes(f.dataset.rvf)) renderBrain(true);
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
}

/* The Brain Agent's half of the shared panel (task 205): its buttons and
   its fields, wired once on the panel. Typing is kept on the conversation
   it was typed into and sends nothing until a button. */
function wireAgentPanel(el) {
  el.addEventListener('click', (ev) => {
    const b = ev.target.closest && ev.target.closest('[data-bact]');
    if (!b || !el.contains(b) || b.disabled || !b.dataset.bact.startsWith('ag-')) return;
    const s = activeSlot();
    if (!s || s.purpose !== 'brain') return;
    ag = s.data;
    act(b.dataset.bact, b.dataset.barg, b);
  });
  const onField = (ev) => {
    const s = activeSlot();
    if (!s || s.purpose !== 'brain') return;
    ag = s.data;
    agField(ev.target, ev, el);
  };
  el.addEventListener('input', onField);
  el.addEventListener('change', onField);
  // a request the Brain Agent is working on is read again while it works,
  // wherever the person is; one waiting on the person is not
  setInterval(() => {
    for (const s of slotsOf('brain')) {
      if (!s.data.draftId) continue;
      const slot = drafts.one[s.data.draftId];
      const d = slot && slot.data;
      if (slot && slot.gone) continue;
      if (!d || ['running', 'applying'].includes(d.status)) loadDraft(s.data.draftId);
    }
  }, 2000);
}

function agField(f, ev, el) {
  if (!ag || !f.dataset || !(f.dataset.ag || f.dataset.agq || f.dataset.age)) return false;
  // the Brain Agent's fields: kept here, sent nothing until a button
  if (f.dataset.ag === 'text') {
    // a changed request is a new submission; the same text resent is not
    if (ag.mode === 'compose' && ag.text !== f.value && ev.type === 'input') ag.request = agRequest();
    ag.text = f.value;
  } else if (f.dataset.ag === 'note') ag.note = f.value;
  else if (f.dataset.agq) ag.answers[f.dataset.agq] = f.value;
  else if (f.dataset.age && ag.edit) ag.edit[f.dataset.age] = f.value;
  if (ev.type === 'change') savePanel();
  // Generate becomes available once there is a request; the preflight
  // is asked again for the words typed (a read) when the field is left.
  // Leaving it is usually a press on Generate, so nothing is forced:
  // the repaint waits for that click to be dispatched
  if (ev.type === 'change' && f.dataset.ag === 'text' && ag.mode === 'compose') {
    if (!ag.target) loadPreflight(ag);
    renderBrain();
  } else if (ev.type === 'input' && f.dataset.ag === 'text') {
    const gen = el.querySelector('[data-bact="ag-generate"]');
    const blocked = ag.preflight && ag.preflight.blockers && ag.preflight.blockers.length;
    if (gen) gen.disabled = ag.busy || !ag.text.trim() || !!blocked;
  } else if (ev.type === 'change' && f.dataset.age === 'classification') renderBrain(true);
  return true;
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

/* A link written in an entry (or a review's report) is resolved against
   that page's own address, so `[the other one](e-other)` reaches
   `/brain/e/e-other`. One
   to the board's own pages is followed in this tab like any other link
   here; anything else opens in a new one. md() already refused every
   scheme but the web, mail and anchors. */
function settleLinks(el, at) {
  const base = new URL(at, location.origin);
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
  if (red && red.key !== k) red = null;
  if (hb && k !== 'index') hb = null;
  if (ad && k !== 'index') { ad = null; delete ops[ADOPT_OP]; }
  // held while a person types, and while a pointer is down on the view
  // `live` (the Brain Agent's preflight arriving) paints under a person's
  // typing — the caret is kept below — but never under a pressed pointer
  const typing = isField(document.activeElement) && el.contains(document.activeElement);
  // the shared panel paints by its own rules (it holds under its own typing)
  if (activeSlot() && activeSlot().purpose === 'brain') renderPanel(force === true || force === 'live' ? force : false);
  if (force === 'live' ? pressing().held() : !force && (typing || pressing().held())) return;
  let html;
  // a destination has not arrived until its reading has: an entry's own
  // markup may hold other loading notes (its Revisions fold), so this is
  // said, never searched for in the markup
  let loading = false;
  if (k === 'seed') {
    const seed = seedHtml();
    html = seed.html;
    loading = seed.loading;
  } else if (S.brainBad) {
    html = back + '<div class="b-panel" role="alert"><h1 class="b-title" tabindex="-1">Not a Brain address</h1>' +
      `<p><code>${esc(S.brainBad)}</code> does not name an entry or a review. An entry's address is ` +
      '<code>/brain/e/&lt;id&gt;</code>, its id in lower-case letters, digits and hyphens; a review filed ' +
      'on a card is <code>/brain/r/&lt;card&gt;/&lt;review&gt;</code>, its identity <code>rv-</code> or ' +
      '<code>rl-</code> and ten hex digits.</p></div>';
  } else if (k.startsWith('r:')) {
    const slot = cache.entries[k];
    // a form open on the page holds the live re-read: its record is what
    // the Save is held to, and a newer one is the server's to refuse
    if (!slot || (!red && Date.now() - slot.at > FRESH_MS)) load(k);
    loading = !slot;
    html = !slot ? '<p class="b-loading" role="status" aria-busy="true">Reading the review…</p>'
      : reviewHtml(slot, k);
  } else {
    const slot = k === 'index' ? cache.index : cache.entries[k];
    if (!slot || Date.now() - slot.at > FRESH_MS) load(k);
    if (k === 'index' && (!cache.reviews || Date.now() - cache.reviews.at > FRESH_MS)) load('reviews');
    // the handbook is not re-read under a person organizing it: the Save is
    // held to the revision it started from, and a newer one is the server's
    // to refuse, with both orders shown
    if (k === 'index' && !hb && (!cache.handbook || Date.now() - cache.handbook.at > FRESH_MS)) {
      load('handbook');
    }
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
    // the Brain Agent's request is drawn in the shared panel beside the
    // page (task 205), so the entry it is about stays in view
  }
  const arrived = k !== paintedKey && !loading;
  if (html !== painted) {
    // a live repaint keeps the reader's place: replaced only when the
    // reading changed, and focus is put back where it was
    const active = document.activeElement;
    // the caret is kept in the search box and in a draft's text fields (a
    // forced repaint — the current version read in — lands while typing)
    const caret = active && active.dataset && (active.dataset.bfind === 'q' ||
      (active.dataset.skey && (active.tagName === 'TEXTAREA' || active.type === 'text' ||
        active.type === 'search')) ||
      ((active.dataset.bf || active.dataset.ag || active.dataset.agq || active.dataset.age || active.dataset.rvf ||
        active.dataset.hb || active.dataset.ad) &&
        (active.tagName === 'TEXTAREA' || active.type === 'text')))
      ? [active.selectionStart, active.selectionEnd] : null;
    const keep = active && el.contains(active) && active.dataset
      ? (active.classList.contains('b-conflict') ? '.b-conflict'
        : active.dataset.skey ? `[data-skey="${CSS.escape(active.dataset.skey)}"]`
        : active.dataset.sact ? `[data-sact="${CSS.escape(active.dataset.sact)}"]`
        : active.dataset.bfind ? `[data-bfind="${CSS.escape(active.dataset.bfind)}"]`
        : active.dataset.bkind ? `[data-bkind="${CSS.escape(active.dataset.bkind)}"]`
        : active.dataset.bshow ? `[data-bshow="${CSS.escape(active.dataset.bshow)}"]`
        : active.dataset.rvf ? `[data-rvf="${CSS.escape(active.dataset.rvf)}"]`
        : active.dataset.ag ? `[data-ag="${CSS.escape(active.dataset.ag)}"]`
        : active.dataset.agq ? `[data-agq="${CSS.escape(active.dataset.agq)}"]`
        : active.dataset.age && active.type !== 'radio' ? `[data-age="${CSS.escape(active.dataset.age)}"]`
        : active.dataset.hb ? `[data-hb="${CSS.escape(active.dataset.hb)}"]`
        : active.dataset.ad ? `[data-ad="${CSS.escape(active.dataset.ad)}"]`
        : active.dataset.adop ? `[data-adop="${CSS.escape(active.dataset.adop)}"]`
        : active.dataset.hbrep ? `[data-hbrep="${CSS.escape(active.dataset.hbrep)}"]`
        : active.dataset.brainId ? `[data-brain-id="${CSS.escape(active.dataset.brainId)}"]`
        : active.dataset.bact ? `[data-bact="${CSS.escape(active.dataset.bact)}"]` +
          (active.dataset.barg ? `[data-barg="${CSS.escape(active.dataset.barg)}"]` : '')
          : active.dataset.bf ? `[data-bf="${CSS.escape(active.dataset.bf)}"]` +
            (active.type === 'radio' ? `[value="${CSS.escape(active.value)}"]` : '') : null) : null;
    el.innerHTML = `<div class="b-main">${html}</div>`;
    painted = html;
    if (S.brainReview) settleLinks(el, reviewHref(S.brainReview.card, S.brainReview.id));
    else if (S.brainEntry) settleLinks(el, entryHref(S.brainEntry));
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
    // the library's row first; a handbook link where the library hides it
    const target = (k === 'index' && returnTo && (el.querySelector(`.b-lib .b-list [data-brain-id="${CSS.escape(returnTo)}"]`) ||
      el.querySelector(`.b-list [data-brain-id="${CSS.escape(returnTo)}"]`)))
      || el.querySelector('.b-title');
    if (target && document.activeElement !== target) target.focus();
    if (k !== 'index') { el.scrollTop = 0; if (k !== 'seed') returnTo = k.slice(2); }
  }
}

export {
  registerBrainAgent, renderBrain
};
