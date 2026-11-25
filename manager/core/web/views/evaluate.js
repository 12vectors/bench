/* Evaluate classifications (task 196): a panel on the Brain's list.

   A person selects up to eight maintained entries and asks what an
   evaluation would ride (`POST /api/brain/evaluate/preflight`, a read):
   each entry's classification, revision and any manual override, the
   cards they cite and whether those are pinned, the harness, model and
   profile, every source and its revision, the budget and the approval
   digest. **Evaluate** — armed, then fired — sends that approval
   (`POST /api/brain/evaluate/start`) and launches one read-only round
   through the Brain Agent's own machinery; its progress, questions and
   proposals are read back from `/api/brain/drafts`. Continue, Cancel and
   Discard are the drafting routes.

   **Review proposals** asks the board (`POST /api/brain/evaluate/review`,
   a read) what Apply would write for the person's choices: per entry the
   classification before and after, the reason, the bound evidence, the
   reassessment signals said as signals, what is missing, the Current
   explanation linked or pending, and why an entry cannot be applied (a
   manual override, a changed entry or source). **Apply** — armed, then
   fired — sends that review's `preview` digest with the same choices
   under one request identity (`POST /api/brain/evaluate/apply`); its
   saved, committed and shared receipts are read from
   `/api/brain/operation`.

   Every string from the server goes through `esc`: a run's words are
   data, never markup. */
import { fetch, post } from '../lib/api.js';
import { esc } from '../lib/dom.js';

const MAX = 8;
const CLASSES = ['Current', 'Historical', 'Superseded', 'Unclassified'];
const STATUS_WORDS = { running: 'Evaluating', questions: 'Waiting for your answers',
  ready: 'Ready to continue', proposed: 'Proposals ready', applying: 'Applying',
  applied: 'Applied', failed: 'Failed', cancelled: 'Cancelled', discarded: 'Discarded' };
const BLOCK_WORDS = { override: 'Manual override holds', 'destination-changed': 'Entry changed',
  'source-drift': 'Evidence changed', 'no-change': 'No change', gone: 'Entry gone',
  'needs-replacement': 'Needs a replacement', 'needs-reason': 'Needs a reason',
  invalid: 'Not a classification', 'invalid-explanation': 'Not a Current explanation',
  'reason-too-long': 'Reason too long' };
const PUSH_WORDS = { shared: 'Shared — the remote holds it', pending: 'Committed locally; sync pending',
  failed: 'Push failed', off: 'Not shared — BOARD_SYNC is off', 'not-committed': 'Not shared',
  conflict: 'Remote conflict' };

// the page's one evaluation session: kept across repaints and a visit to
// an entry, dropped by a reload — and resumed from the board after one
const ev = { open: false, picked: new Set(), pre: null, preBusy: false, preError: null,
  note: '', armed: false, startBusy: false, startError: null, startKey: null,
  draft: null, adopted: false, answers: {}, contNote: '', stepBusy: false, poll: null,
  choices: new Map(), review: null, reviewBusy: false, reviewError: null,
  applyArmed: false, applyBusy: false, applyError: null, applyKey: null,
  op: null, opRequest: null, opBusy: false, opError: null };
let repaint = () => {};
let wired = false;

function setEvalRepaint(fn) { repaint = fn; }

const short = (s, n = 12) => s ? String(s).slice(0, n) : '—';
const key = (p) => `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

async function resume() {
  // a reload or a restart resumes the newest open evaluation the board holds
  ev.adopted = true;
  try {
    const res = await fetch('/api/brain/drafts');
    const list = await res.json().catch(() => ({}));
    const open = (list.evaluations || []).find(x => !['discarded', 'cancelled', 'applied'].includes(x.status));
    if (open && !ev.draft) { adopt(open); ev.open = true; repaint(true); watch(); }
  } catch (e) { /* the list is a convenience; the panel works without it */ }
}

function adopt(draft) {
  const before = ev.draft;
  if (before && (before.id !== draft.id || before.proposalRound !== draft.proposalRound)) {
    ev.review = null;
    ev.choices = new Map();
  }
  ev.draft = draft;
  if (draft.status === 'applying' && draft.evaluate && draft.evaluate.applyRequest) ev.applyKey = draft.evaluate.applyRequest;
  if (draft.status === 'applied' && draft.applied) ev.opRequest = draft.applied.request;
}

async function readDraft() {
  if (!ev.draft) return;
  try {
    const res = await fetch(`/api/brain/drafts?id=${encodeURIComponent(ev.draft.id)}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.draft) adopt(data.draft);
  } catch (e) { /* asked again on the next beat */ }
  repaint(true);
  watch();
}

function watch() {
  if (ev.poll) { clearTimeout(ev.poll); ev.poll = null; }
  if (ev.draft && ['running', 'applying'].includes(ev.draft.status)) ev.poll = setTimeout(readDraft, 2000);
}

async function preflight() {
  if (!ev.picked.size || ev.preBusy) return;
  ev.preBusy = true;
  ev.preError = null;
  ev.armed = false;
  repaint(true);
  const res = await post('/api/brain/evaluate/preflight', { entries: [...ev.picked] });
  ev.preBusy = false;
  if (res.ok) ev.pre = res.data.preflight; else { ev.pre = null; ev.preError = res.error; }
  repaint(true);
  document.getElementById('ev-pre')?.focus();
}

async function start() {
  if (!ev.pre || !ev.pre.ready || ev.startBusy) return;
  if (!ev.armed) { ev.armed = true; repaint(true); return; }
  ev.armed = false;
  ev.startBusy = true;
  ev.startError = null;
  repaint(true);
  // one identity per Evaluate: a resend after a lost reply answers, never launches twice
  ev.startKey = ev.startKey || key('evaluate');
  const res = await post('/api/brain/evaluate/start', { request: ev.startKey,
    entries: ev.pre.entries.map(e => e.id), approval: ev.pre.approval, text: ev.note });
  ev.startBusy = false;
  if (res.ok) {
    adopt(res.data.draft);
    ev.startKey = null;
    watch();
  } else {
    ev.startError = res.error;
    if (res.status !== 0) ev.startKey = null;
    if (res.data && res.data.stale) ev.pre = null;
  }
  repaint(true);
  document.getElementById(ev.draft ? 'ev-draft' : 'ev-pre')?.focus();
}

async function step(name) {
  const d = ev.draft;
  if (!d || ev.stepBusy) return;
  const body = { id: d.id };
  if (name === 'continue') {
    body.request = key('evaluate-c');
    body.round = d.round;
    if (d.status === 'questions') body.answers = { ...ev.answers };
    if (ev.contNote.trim()) body.note = ev.contNote.trim();
  }
  ev.stepBusy = true;
  repaint(true);
  const res = await post(`/api/brain/draft/${name}`, body);
  ev.stepBusy = false;
  if (res.ok && res.data.draft) {
    adopt(res.data.draft);
    ev.answers = {};
    ev.contNote = '';
    ev.startError = null;
  } else {
    ev.startError = res.error;
  }
  repaint(true);
  watch();
}

const choiceList = () => [...ev.choices.entries()].map(([entry, c]) => ({ entry, ...c }));

async function review() {
  const d = ev.draft;
  if (!d || ev.reviewBusy) return;
  ev.reviewBusy = true;
  ev.reviewError = null;
  ev.applyArmed = false;
  repaint(true);
  const res = await post('/api/brain/evaluate/review', { id: d.id, choices: choiceList() });
  ev.reviewBusy = false;
  if (res.ok) ev.review = res.data.review; else { ev.review = null; ev.reviewError = res.error; }
  repaint(true);
}

async function apply() {
  const d = ev.draft;
  const r = ev.review;
  if (!d || !r || !r.ready || ev.applyBusy) return;
  if (!ev.applyArmed) { ev.applyArmed = true; repaint(true); return; }
  ev.applyArmed = false;
  ev.applyBusy = true;
  ev.applyError = null;
  repaint(true);
  ev.applyKey = ev.applyKey || key('evaluate-apply');
  const res = await post('/api/brain/evaluate/apply', { id: d.id, request: ev.applyKey,
    round: d.proposalRound, preview: r.preview, choices: choiceList() });
  ev.applyBusy = false;
  if (res.ok) {
    adopt(res.data.draft);
    ev.op = res.data.operation || null;
    ev.opRequest = ev.applyKey;
    ev.applyKey = null;
  } else {
    ev.applyError = res.error;
    // no answer at all: the same identity again asks what happened, never writes twice
    if (res.status !== 0) ev.applyKey = null;
    if (res.data && res.data.review) ev.review = res.data.review;
    readDraft();
  }
  repaint(true);
  document.getElementById(res.ok ? 'ev-outcome' : 'ev-review')?.focus();
}

async function readOp() {
  const k = ev.opRequest || (ev.draft && ev.draft.applied && ev.draft.applied.request);
  if (!k || ev.opBusy) return;
  ev.opRequest = k;
  ev.opBusy = true;
  try {
    const res = await fetch(`/api/brain/operation?request=${encodeURIComponent(k)}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) ev.op = data.operation; else ev.opError = data.error || `the board answered ${res.status}`;
  } catch (e) { ev.opError = 'the board could not be asked'; }
  ev.opBusy = false;
  repaint(true);
}

async function retryOp(action) {
  if (!ev.opRequest || ev.opBusy) return;
  ev.opBusy = true;
  ev.opError = null;
  repaint(true);
  const res = await post('/api/brain/retry', { request: ev.opRequest, action });
  ev.opBusy = false;
  if (res.ok) ev.op = res.data.operation || res.data; else ev.opError = res.error;
  repaint(true);
}

/* ── what is drawn ──────────────────────────────────────────────── */

function pickerHtml(entries) {
  const live = entries.filter(e => !e.tombstone && e.id && e.kind !== 'Review' && !e.review);
  if (!live.length) return '<p class="b-note">There are no maintained entries to evaluate yet.</p>';
  const full = ev.picked.size >= MAX;
  return `<fieldset class="ev-pick"><legend>Entries to evaluate (at most ${MAX})</legend>` +
    `<ul class="sd-list">${live.map(e => {
      const id = `ev-p-${esc(e.id)}`;
      const on = ev.picked.has(e.id);
      return `<li><input type="checkbox" id="${id}" data-evpick="${esc(e.id)}"${on ? ' checked' : ''}` +
        `${!on && full ? ' disabled' : ''}> <label for="${id}">${esc(e.title || e.id)} ` +
        `<span class="b-note">· ${esc(e.classification || '—')} · <code>${esc(e.id)}</code></span></label></li>`;
    }).join('')}</ul></fieldset>`;
}

function preflightHtml() {
  const p = ev.pre;
  const ask = `<div class="sd-acts"><button type="button" class="b-btn" data-evact="preflight"` +
    `${!ev.picked.size || ev.preBusy ? ' disabled' : ''}${ev.preBusy ? ' aria-busy="true"' : ''}>` +
    `${ev.preBusy ? 'Reading…' : 'Review what Evaluate would use'}</button></div>` +
    (ev.preError ? `<p class="b-missing" role="alert">${esc(ev.preError)}</p>` : '');
  if (!p) return ask;
  const sel = p.selection || {};
  const model = (sel.model && sel.model.value) || 'the harness default model';
  const harness = (sel.harness && sel.harness.value) || p.adapter || '—';
  const blockers = (p.blockers || []).map(b => `<li>${esc(b)}</li>`).join('');
  const cards = (p.cards || []).map(c => `<li>#${esc(c.card)} (for <code>${esc(c.for)}</code>) — ` +
    `${c.pinned ? `pinned, in ${esc(c.stage)}` : 'not in the bounded selection: a known gap'}</li>`).join('');
  return ask + '<div class="b-panel" id="ev-pre" tabindex="-1" aria-labelledby="ev-h-pre">' +
    '<h3 id="ev-h-pre">What Evaluate would use</h3><dl class="sd-facts">' +
    `<dt>Harness and model</dt><dd>${esc(harness)} · ${esc(model)}</dd>` +
    `<dt>Entries</dt><dd><ul class="sd-list">${p.entries.map(e => `<li><code>${esc(e.id)}</code> — ` +
      `${esc(e.classification)}, revision <code>${esc(short(e.revision, 16))}</code>` +
      `${e.override ? ' · <b>manual override: shown, never applied over</b>' : ''}</li>`).join('')}</ul></dd>` +
    `<dt>Related cards</dt><dd>${cards ? `<ul class="sd-list">${cards}</ul>` : 'none cited'}</dd>` +
    `<dt>Sources</dt><dd>${p.work.sources} work-root file(s), pinned read-only and granted by exact ` +
    `reads; the application at <code>${esc(short(p.app.commit))}</code>; ${p.work.excludedCount} left out</dd>` +
    `<dt>Budget</dt><dd>${p.budget.rounds} round(s) of at most ${p.budget.secondsPerRound} s each</dd>` +
    `<dt>Authority</dt><dd>${esc(p.authority)}</dd>` +
    `<dt>Enforcement</dt><dd>${esc(p.enforcement)}</dd>` +
    `<dt>Approval</dt><dd><code>${esc(short(p.approval, 16))}</code></dd></dl>` +
    `<details class="sd-excl"><summary>The rules each proposal is held to</summary><ul class="sd-list">` +
    p.rules.map(r => `<li>${esc(r)}</li>`).join('') + '</ul></details>' +
    `<details class="sd-excl"><summary>Every source and its revision (${p.work.list.length})</summary>` +
    `<ul class="sd-list">${p.work.list.map(s => `<li><code>${esc(s.ref)}</code> ` +
      `<span class="b-note">sha256 ${esc(s.sha256)}</span></li>`).join('')}</ul></details>` +
    (blockers ? `<div class="b-panel b-bad" role="alert"><p><b>Evaluate is not available here.</b></p>` +
      `<ul class="sd-list">${blockers}</ul></div>` : '') +
    `<p><label for="ev-note">A note for the agent (optional)</label><br>` +
    `<textarea id="ev-note" rows="2" class="sd-wide" data-evnote>${esc(ev.note)}</textarea></p>` +
    '<div class="sd-acts">' +
    `<button type="button" class="b-btn b-primary${ev.armed ? ' armed' : ''}" data-evact="start"` +
    `${p.ready && !ev.startBusy ? '' : ' disabled'}${ev.startBusy ? ' aria-busy="true"' : ''}>` +
    `${ev.startBusy ? 'Launching…' : ev.armed ? 'Confirm: launch one read-only evaluation run' : 'Evaluate'}</button>` +
    (ev.armed ? '<button type="button" class="b-btn" data-evact="disarm">Not now</button>' : '') +
    '</div>' + (ev.startError ? `<p class="b-missing" role="alert">${esc(ev.startError)}</p>` : '') +
    '<p class="b-note">Evaluate launches one bounded run and writes nothing; only your separate Apply ' +
    'changes a classification.</p></div>';
}

function progressHtml(d) {
  const g = d.progress || {};
  const items = (g.done || []).map(x => `<li>${esc(x)}</li>`).join('');
  return '<dl class="sd-facts">' +
    `<dt>Done</dt><dd><ul class="sd-list">${items || '<li>—</li>'}</ul></dd>` +
    `<dt>Now</dt><dd>${esc(g.doing || '—')}</dd>` +
    `<dt>Remaining</dt><dd>${esc((g.remaining || []).join(' · ') || '—')}</dd>` +
    (g.blocked ? `<dt>Blocked</dt><dd>${esc(g.blocked)}</dd>` : '') +
    (g.toolActivity && g.toolActivity.last ? `<dt>Tool activity</dt><dd class="b-note">` +
      `${esc(g.toolActivity.last.summary || '')} — activity, not progress</dd>` : '') + '</dl>';
}

function draftHtml() {
  const d = ev.draft;
  if (!d) return '';
  const word = STATUS_WORDS[d.status] || d.status;
  const err = d.error ? `<p class="b-missing" role="alert">${esc(d.error.why || '')}</p>` : '';
  const conflict = d.conflict ? `<p class="b-warn" role="status">Apply was refused: ` +
    `<b>${esc(d.conflict.code)}</b> — ${esc(d.conflict.why || '')}</p>` : '';
  const qs = d.status === 'questions' ? (d.questions || []).map(q =>
    `<p><label for="ev-q-${esc(q.id)}">${esc(q.question)}</label><br><span class="b-note">${esc(q.why || '')}</span><br>` +
    `<textarea id="ev-q-${esc(q.id)}" rows="2" class="sd-wide" data-evanswer="${esc(q.id)}">` +
    `${esc(ev.answers[q.id] || '')}</textarea></p>`).join('') : '';
  const canCont = d.canContinue && d.status !== 'running';
  const acts = '<div class="sd-acts">' +
    (d.status === 'running' ? `<button type="button" class="b-btn" data-evact="cancel"${ev.stepBusy ? ' disabled' : ''}>Cancel</button>` : '') +
    (canCont ? `<button type="button" class="b-btn" data-evact="continue"${ev.stepBusy ? ' disabled' : ''}>` +
      `${d.status === 'questions' ? 'Continue with these answers' : d.status === 'failed' ? 'Try again' : 'Ask for a revised evaluation'}</button>` : '') +
    (!['running', 'applying', 'applied', 'discarded', 'cancelled'].includes(d.status)
      ? `<button type="button" class="b-btn" data-evact="discard"${ev.stepBusy ? ' disabled' : ''}>Discard</button>` : '') +
    (['applied', 'discarded', 'cancelled', 'failed'].includes(d.status)
      ? '<button type="button" class="b-btn" data-evact="new">Start a new evaluation</button>' : '') + '</div>';
  const note = canCont && d.status !== 'questions'
    ? `<p><label for="ev-cnote">What to look at again (optional)</label><br><textarea id="ev-cnote" rows="2" ` +
      `class="sd-wide" data-evcnote>${esc(ev.contNote)}</textarea></p>` : '';
  const evn = (d.evaluate || {}).evaluation;
  const refused = evn && (evn.refused || []).length ? `<details class="sd-excl"><summary>Proposals bench refused ` +
    `(${evn.refused.length})</summary><ul class="sd-list">${evn.refused.map(r => `<li><code>` +
    `${esc(r.entry || 'another entry')}</code> — ${esc(r.problems.join('; '))}</li>`).join('')}</ul></details>` : '';
  const missing = evn && (evn.missing || []).length ? `<p class="b-note">Not evaluated by the run: ` +
    `${evn.missing.map(m => `<code>${esc(m)}</code>`).join(', ')}.</p>` : '';
  return `<section class="b-panel" id="ev-draft" tabindex="-1" aria-labelledby="ev-h-draft">` +
    `<h3 id="ev-h-draft">Evaluation <code>${esc(d.id)}</code> — <span role="status">${esc(word)}</span></h3>` +
    progressHtml(d) + err + conflict + qs + note + refused + missing + acts +
    `<p class="b-note">${esc(d.local || '')}</p></section>`;
}

function rowHtml(row) {
  const id = `ev-r-${esc(row.entry)}`;
  const c = ev.choices.get(row.entry) || {};
  const klass = c.classification || row.classification;
  const expl = row.explanation;
  const explWords = !expl ? '' : expl.state === 'linked'
    ? `Explained now by <a href="/brain/e/${encodeURIComponent(expl.entry)}" data-route>${esc(expl.title || expl.entry)}</a>`
    : '<b>Current explanation pending</b> — no maintained explanation exists yet; none is invented' +
      (expl.was ? ` (<code>${esc(expl.was)}</code> is no longer Current)` : '');
  const evidence = (row.evidence || []).map(b => `<li><code>${esc(b.ref)}</code> <span class="b-note">sha256 ` +
    `${esc(short(b.sha256))}${b.commit ? ` at ${esc(short(b.commit))}` : ''}</span>` +
    `${b.drifted ? ' <span class="b-missing">changed since it was read</span>' : ''}</li>`).join('');
  const signals = (row.signals || []).map(s => `<li>${esc(s)}</li>`).join('');
  const blocked = row.blocked ? `<p class="b-warn" role="status"><b>${esc(BLOCK_WORDS[row.blocked.code] || row.blocked.code)}.</b> ` +
    `${esc(row.blocked.why)}</p>` : '';
  const options = CLASSES.map(k => `<option value="${k}"${k === klass ? ' selected' : ''}>${k}</option>`).join('');
  return `<article class="b-panel ev-row" aria-labelledby="${id}">` +
    `<h4 id="${id}"><a href="/brain/e/${encodeURIComponent(row.entry)}" data-route>${esc(row.title || row.entry)}</a></h4>` +
    `<p class="sd-row"><input type="checkbox" id="${id}-sel" data-evchoice="${esc(row.entry)}" data-evfield="selected"` +
    `${row.selected ? ' checked' : ''}> <label for="${id}-sel">Apply: <b>${esc(row.current)}</b> → <b>${esc(klass)}</b>` +
    `${row.corrected ? ' (your correction)' : ''}</label></p>` + blocked +
    '<dl class="sd-facts">' +
    `<dt>Proposed</dt><dd>${esc(row.proposed)} — <span class="b-note">the Brain Agent's proposal, validated by bench</span></dd>` +
    `<dt>Reason</dt><dd>${esc(row.reason || '—')}</dd>` +
    (expl ? `<dt>Explanation</dt><dd>${explWords}</dd>` : '') +
    (row.missing ? `<dt>Missing evidence</dt><dd>${esc(row.missing)}</dd>` : '') +
    (row.uncertainty ? `<dt>Uncertain</dt><dd>${esc(row.uncertainty)}</dd>` : '') +
    (row.override ? `<dt>Manual override</dt><dd>${esc(row.override)}</dd>` : '') +
    `<dt>Evidence</dt><dd>${evidence ? `<ul class="sd-list">${evidence}</ul>` : 'none bound'}</dd>` +
    (signals ? `<dt>Reassessment signals</dt><dd><ul class="sd-list">${signals}</ul><span class="b-note">` +
      'A stage, an edit or a PR is a reason to look again, never proof.</span></dd>' : '') + '</dl>' +
    `<p><label for="${id}-k">Classification</label> <select id="${id}-k" data-evchoice="${esc(row.entry)}" ` +
    `data-evfield="classification">${options}</select></p>` +
    `<p><label for="${id}-why">Reason (kept in the entry's history)</label><br><input type="text" ` +
    `id="${id}-why" class="sd-wide" data-evchoice="${esc(row.entry)}" data-evfield="reason" ` +
    `value="${esc(c.reason !== undefined ? c.reason : row.reason || '')}"></p>` +
    (klass === 'Historical' ? `<p><label for="${id}-ex">Explained now by (a Current entry's id; ` +
      `empty leaves it pending)</label> <input type="text" id="${id}-ex" data-evchoice="${esc(row.entry)}" ` +
      `data-evfield="explanation" value="${esc(c.explanation ? c.explanation.entry
        : expl && expl.state === 'linked' ? expl.entry : '')}"></p>` : '') +
    (klass === 'Superseded' ? `<p><label for="${id}-by">Replaced by (entry id)</label> <input type="text" ` +
      `id="${id}-by" data-evchoice="${esc(row.entry)}" data-evfield="supersededBy" ` +
      `value="${esc(c.supersededBy !== undefined ? c.supersededBy : row.supersededBy || '')}"> ` +
      `<label for="${id}-inv">or the invalidation</label> <input type="text" id="${id}-inv" ` +
      `data-evchoice="${esc(row.entry)}" data-evfield="invalidation" ` +
      `value="${esc(c.invalidation !== undefined ? c.invalidation : row.invalidation || '')}"></p>` : '') +
    '</article>';
}

function reviewHtml() {
  const d = ev.draft;
  if (!d || !['proposed', 'applying'].includes(d.status)) return '';
  const r = ev.review;
  const head = '<section class="b-panel" id="ev-review" tabindex="-1" aria-labelledby="ev-h-review">' +
    '<h3 id="ev-h-review">Review and apply</h3><p>Correct and choose the classifications to apply. ' +
    'Apply is separate: one batch, all or nothing, changing classification metadata only.</p>';
  if (!r) {
    return head + `<div class="sd-acts"><button type="button" class="b-btn b-primary" data-evact="review"` +
      `${ev.reviewBusy ? ' disabled aria-busy="true"' : ''}>${ev.reviewBusy ? 'Reading…' : 'Review proposals'}</button></div>` +
      (ev.reviewError ? `<p class="b-missing" role="alert">${esc(ev.reviewError)}</p>` : '') + '</section>';
  }
  const pub = r.publication || {};
  const blockers = (r.blockers || []).map(b => `<li><b>${esc(b.code)}</b>${b.entry ? ` <code>${esc(b.entry)}</code>` : ''} — ${esc(b.why)}</li>`).join('');
  const label = `Apply ${r.selected} classification${r.selected === 1 ? '' : 's'}`;
  return head + r.items.map(rowHtml).join('') +
    '<dl class="sd-facts">' +
    `<dt>Writes</dt><dd>${(pub.writes || []).map(w => `<code>${esc(w)}</code>`).join(', ') || 'nothing'}</dd>` +
    `<dt>Changes</dt><dd>${esc(pub.changes || '')}</dd>` +
    `<dt>Commit</dt><dd>${esc(pub.commit || '')}</dd><dt>Sharing</dt><dd>${esc(pub.sync || '')}</dd>` +
    `<dt>Never</dt><dd>${esc(pub.never || '')}</dd></dl>` +
    (blockers ? `<div class="b-panel b-bad" role="alert"><p><b>Apply is not available.</b></p><ul class="sd-list">${blockers}</ul></div>` : '') +
    (ev.applyError ? `<p class="b-missing" role="alert">${esc(ev.applyError)}</p>` : '') +
    '<div class="sd-acts">' +
    `<button type="button" class="b-btn" data-evact="review"${ev.reviewBusy ? ' disabled aria-busy="true"' : ''}>` +
    `${ev.reviewBusy ? 'Reading…' : 'Review again'}</button>` +
    `<button type="button" class="b-btn b-primary${ev.applyArmed ? ' armed' : ''}" data-evact="apply"` +
    `${r.ready && !ev.applyBusy ? '' : ' disabled'}${ev.applyBusy ? ' aria-busy="true"' : ''}>` +
    `${ev.applyBusy ? 'Applying…' : ev.applyArmed ? `Confirm: ${label.toLowerCase()} as one batch` : label}</button>` +
    (ev.applyArmed ? '<button type="button" class="b-btn" data-evact="apply-disarm">Not now</button>' : '') +
    '</div></section>';
}

function outcomeHtml() {
  const d = ev.draft;
  if (!d || d.status !== 'applied') return '';
  if (!ev.op && !ev.opBusy && !ev.opError) readOp();
  const op = ev.op;
  const changes = (d.applied && d.applied.classifications) || {};
  const commit = op ? op.commit || {} : {};
  const push = op ? op.push || {} : {};
  const commitWord = !op ? 'Reading…' : commit.state === 'committed'
    ? `Committed locally <code>${esc(short(commit.sha))}</code>`
    : commit.state === 'off' ? 'Applied locally; not committed (BOARD_COMMIT_MOVES is off)'
    : `Applied locally; commit pending — ${esc(commit.detail || 'the commit failed')}`;
  return '<section class="b-panel" id="ev-outcome" tabindex="-1" aria-labelledby="ev-h-out">' +
    '<h3 id="ev-h-out">Applied</h3>' +
    `<ul class="sd-list" role="status">${Object.entries(changes).map(([id, k]) =>
      `<li><a href="/brain/e/${encodeURIComponent(id)}" data-route><code>${esc(id)}</code></a> → ${esc(k)}</li>`).join('')}</ul>` +
    '<dl class="sd-facts">' +
    `<dt>Saved</dt><dd>${op ? (op.saved ? 'Applied locally' : 'Not saved') : 'Reading…'}</dd>` +
    `<dt>Committed</dt><dd>${commitWord}</dd>` +
    `<dt>Shared</dt><dd>${esc(PUSH_WORDS[push.state] || push.state || '—')}` +
    `${push.detail ? `<br><span class="b-note">${esc(push.detail)}</span>` : ''}</dd></dl>` +
    (ev.opError ? `<p class="b-missing" role="alert">${esc(ev.opError)}</p>` : '') +
    '<div class="sd-acts">' +
    (op && commit.state === 'failed' ? '<button type="button" class="b-btn" data-evact="op-commit">Retry commit</button>' : '') +
    (op && ['pending', 'failed'].includes(push.state) ? `<button type="button" class="b-btn" data-evact="op-push">${push.state === 'failed' ? 'Retry push' : 'Push now'}</button>` : '') +
    '<button type="button" class="b-btn" data-evact="op-read">Read again</button></div></section>';
}

function evalHtml(entries) {
  if (!ev.adopted) resume();
  const head = '<section class="b-panel" aria-labelledby="ev-h">' +
    '<h2 id="ev-h">Evaluate classifications</h2>' +
    '<p class="b-note">Ask the Brain Agent whether selected entries are still classified right, from ' +
    'the code, the cards and the plans. It proposes with evidence; you correct, choose and apply. ' +
    'An entry with a manual override is never reclassified.</p>';
  if (!ev.open) {
    return head + '<div class="sd-acts"><button type="button" class="b-btn" data-evact="open">' +
      'Choose entries to evaluate</button></div></section>';
  }
  const choosing = !ev.draft || ['discarded', 'cancelled'].includes(ev.draft.status);
  return head + (choosing ? pickerHtml(entries || []) + preflightHtml() : '') +
    draftHtml() + reviewHtml() + outcomeHtml() +
    '<div class="sd-acts"><button type="button" class="b-btn" data-evact="close">Hide</button></div></section>';
}

function act(name) {
  if (name === 'open') { ev.open = true; repaint(true); document.getElementById('ev-h')?.focus(); return; }
  if (name === 'close') { ev.open = false; repaint(true); return; }
  if (name === 'preflight') { preflight(); return; }
  if (name === 'start') { start(); return; }
  if (name === 'disarm') { ev.armed = false; repaint(true); return; }
  if (name === 'continue' || name === 'cancel' || name === 'discard') { step(name); return; }
  if (name === 'new') {
    ev.draft = null; ev.pre = null; ev.armed = false; ev.review = null; ev.choices = new Map();
    ev.op = null; ev.opRequest = null;
    repaint(true); return;
  }
  if (name === 'review') { review(); return; }
  if (name === 'apply') { apply(); return; }
  if (name === 'apply-disarm') { ev.applyArmed = false; repaint(true); return; }
  if (name === 'op-read') { ev.op = null; ev.opError = null; readOp(); return; }
  if (name === 'op-commit') { retryOp('commit'); return; }
  if (name === 'op-push') { retryOp('push'); }
}

/* The panel's own fields: kept here, sent only by their button. */
function evalField(f, e) {
  const ds = f.dataset || {};
  if (ds.evnote !== undefined) { ev.note = f.value; return true; }
  if (ds.evcnote !== undefined) { ev.contNote = f.value; return true; }
  if (ds.evanswer !== undefined) { ev.answers[ds.evanswer] = f.value; return true; }
  if (ds.evpick !== undefined) {
    if (e.type !== 'change') return true;
    if (f.checked && ev.picked.size < MAX) ev.picked.add(ds.evpick); else ev.picked.delete(ds.evpick);
    // a changed selection is a different evaluation: the preview is not its
    ev.pre = null;
    ev.armed = false;
    repaint(true);
    document.getElementById(`ev-p-${ds.evpick}`)?.focus();
    return true;
  }
  if (ds.evchoice !== undefined) {
    const c = ev.choices.get(ds.evchoice) || {};
    if (ds.evfield === 'explanation') {
      // an entry id names the maintained Current explanation; empty leaves it
      // to the review, which says pending when none is named
      const named = f.value.trim();
      if (named) c.explanation = { entry: named }; else delete c.explanation;
    } else {
      c[ds.evfield] = ds.evfield === 'selected' ? f.checked : f.value;
    }
    ev.choices.set(ds.evchoice, c);
    ev.applyArmed = false;
    // a checkbox, a choice or leaving a field asks for the review again, so
    // what Apply would write is always what is shown
    if (e.type === 'change') review();
    return true;
  }
  return false;
}

function wireEvaluate(el) {
  if (wired) return;
  wired = true;
  el.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-evact]');
    if (!b || !el.contains(b) || b.disabled) return;
    act(b.dataset.evact);
  });
}

export { evalField, evalHtml, setEvalRepaint, wireEvaluate };
