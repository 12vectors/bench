/* Automatic classification (task 197): a panel on the Brain's list.

   It reads the Brain reconciliation queue (`GET /api/brain/reconcile`, a
   read): the policy and the two authorities it grants — **evaluate**
   (launch read-only evaluations of entries whose inputs moved) and
   **apply** (apply the proposals bench selects by default, under the
   policy) — whether this installation executes, the budget, and per entry
   its state in words, why, the cards it cites with their revisions, the
   application commit it was evaluated at against the checkout now, and its
   receipts.

   Turning reconciliation on or off and granting either authority is
   `POST /api/brain/reconcile/policy` — armed, then fired, bound to the
   policy revision this page read; it launches nothing. **Rebuild**,
   **Refresh sources**, **Retry** and **Cancel** are
   `POST /api/brain/reconcile/action`, one request identity each. A
   proposal waiting for review opens in Evaluate classifications.

   Every string from the server goes through `esc`. */
import { fetch, post } from '../lib/api.js';
import { esc } from '../lib/dom.js';

const SETTINGS_HEADERS = { 'Content-Type': 'application/json', 'X-Bench-Settings': '1' };
const TONE = { queued: 'b-note', running: 'rc-work', waiting: 'rc-you', review: 'rc-you',
  applied: 'rc-calm', current: 'rc-calm', stale: 'rc-warn', 'sync-pending': 'rc-warn',
  failed: 'rc-bad', blocked: 'rc-bad', cancelled: 'b-note', held: 'b-note', unqueued: 'b-note' };

const rc = { open: false, data: null, busy: false, error: null, armed: null, poll: null,
  acting: false, actError: null, actKey: null };
let repaint = () => {};
let openEval = () => {};
let asked = false;

function setRecHooks(fn, open) { repaint = fn; openEval = open; }

const short = (s, n = 12) => s ? String(s).slice(0, n) : '—';
const key = (p) => `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

async function read() {
  rc.busy = true;
  try {
    const res = await fetch('/api/brain/reconcile');
    const data = await res.json().catch(() => ({}));
    if (res.ok) { rc.data = data.reconcile; rc.error = null; } else rc.error = data.error || `the board answered ${res.status}`;
  } catch (e) { rc.error = 'the board could not be asked'; }
  rc.busy = false;
  repaint(true);
  watch();
}

function watch() {
  if (rc.poll) { clearTimeout(rc.poll); rc.poll = null; }
  // while reconciliation is on the beat may move things at any time, so an
  // open panel reads again every few seconds; off, it reads once
  const on = rc.data && rc.data.effective && rc.data.effective.on;
  if (rc.open && on) rc.poll = setTimeout(read, 4000);
}

async function setPolicy(change) {
  const p = rc.data.policy;
  rc.acting = true;
  rc.actError = null;
  repaint(true);
  let res;
  try {
    res = await fetch('/api/brain/reconcile/policy', { method: 'POST', headers: SETTINGS_HEADERS,
      body: JSON.stringify({ expected: p.revision, ...change }) });
  } catch (e) { res = null; }
  rc.acting = false;
  const data = res ? await res.json().catch(() => ({})) : {};
  if (!res || !res.ok) rc.actError = res ? data.error || `the board answered ${res.status}` : 'the board could not be asked';
  await read();
  document.getElementById('rc-h')?.focus();
}

async function action(name, entries) {
  rc.acting = true;
  rc.actError = null;
  repaint(true);
  rc.actKey = rc.actKey || key(`reconcile-${name}`);
  const body = { action: name, request: rc.actKey };
  if (entries) body.entries = entries;
  const res = await post('/api/brain/reconcile/action', body);
  rc.acting = false;
  if (res.status !== 0) rc.actKey = null;
  if (!res.ok) rc.actError = res.error;
  await read();
}

function armed(name) { return rc.armed === name; }

// the policy changes a button may ask for, by name: the page sends one of
// these and nothing else
const CHANGES = {
  enable: { enabled: true, evaluate: true, apply: false },
  disable: { enabled: false },
  'grant-apply': { apply: true },
  'revoke-apply': { apply: false },
  // task 199: factual explanation updates ride with an applied classification
  'grant-maintain': { maintain: true },
  'revoke-maintain': { maintain: false },
};

function policyBtn(name, label, primary = false) {
  const on = armed(name);
  return `<button type="button" class="b-btn${primary ? ' b-primary' : ''}${on ? ' armed' : ''}" ` +
    `data-rcact="policy" data-rcarg="${esc(name)}"` +
    `${rc.acting ? ' disabled' : ''}>${on ? `Confirm: ${esc(label.toLowerCase())}` : esc(label)}</button>`;
}

function headHtml(d) {
  const p = d.policy;
  const e = d.effective;
  const word = (b) => b ? '<b>granted</b>' : 'not granted';
  const state = p.state === 'unconfigured' ? 'Not configured'
    : p.state === 'invalid' ? 'Invalid — grants nothing' : p.enabled ? 'On' : 'Off';
  const toggles = p.state === 'invalid' ? '' : '<div class="sd-acts">' +
    (!p.enabled || !p.evaluate
      ? policyBtn('enable', 'Turn on: evaluate only', true)
      : policyBtn('disable', 'Turn off')) +
    (p.enabled && p.evaluate && !p.apply
      ? policyBtn('grant-apply', 'Grant the apply authority') : '') +
    (p.apply ? policyBtn('revoke-apply', 'Revoke the apply authority') : '') +
    (p.apply && !p.maintain ? policyBtn('grant-maintain', 'Grant the explanation-update authority') : '') +
    (p.maintain ? policyBtn('revoke-maintain', 'Revoke the explanation-update authority') : '') +
    (rc.armed ? '<button type="button" class="b-btn" data-rcact="disarm">Not now</button>' : '') + '</div>';
  const b = d.budget;
  return '<dl class="sd-facts">' +
    `<dt>Reconciliation</dt><dd><b>${esc(state)}</b> — ${esc(e.why)}</dd>` +
    `<dt>Evaluate authority</dt><dd>${word(e.evaluate)} <span class="b-note">${esc(d.authorities.evaluate)}</span></dd>` +
    `<dt>Apply authority</dt><dd>${word(e.apply)} <span class="b-note">${esc(d.authorities.apply)}</span></dd>` +
    (d.authorities.maintain ? `<dt>Explanation-update authority</dt><dd>${word(e.maintain)} ` +
      `<span class="b-note">${esc(d.authorities.maintain)}</span></dd>` : '') +
    `<dt>Executes here</dt><dd>${e.executes ? 'yes' : `no — ${esc(e.ownerReason || '')}`}</dd>` +
    `<dt>Budget</dt><dd>${b.used} of ${b.dailyLaunches} launches in the last 24 hours; ` +
    `${b.batch} entries an evaluation; ${b.retries} retries; inputs settle ${b.settleSeconds}s first</dd>` +
    `<dt>Policy</dt><dd><code>${esc(p.file)}</code> revision <code>${esc(short(p.revision))}</code>` +
    `${p.actor ? ` · last changed by ${esc(p.actor)} ${esc(p.at || '')}` : ''}` +
    `${p.problem ? ` <span class="b-missing">${esc(p.problem)}</span>` : ''}</dd></dl>` +
    (p.state === 'unconfigured' ? '<p class="b-note">Nothing is queued or launched until you turn it on. ' +
      'Turning it on queues every maintained entry once, launches nothing by itself in this click, and ' +
      'each later launch counts against the budget.</p>' : '') + toggles;
}

function cardsHtml(r) {
  if (!r.cards.length) return 'none cited';
  return r.cards.map(c => `<a href="/cards/${encodeURIComponent(c.card)}" data-route>#${esc(c.card)}</a> ` +
    `<span class="b-note mono">${c.rev ? `${esc(c.stage)} · ${esc(short(c.rev))}` : 'not on this board'}</span>`).join(', ');
}

function rowHtml(r, d) {
  const id = `rc-r-${esc(r.entry)}`;
  const expl = r.explanation;
  const acts = [];
  if (r.stored === 'review' && r.draft) acts.push(`<button type="button" class="b-btn" data-rcact="open" data-rcarg="${esc(r.draft)}">Review in Evaluate</button>`);
  if (['failed', 'blocked', 'cancelled'].includes(r.stored) && d.effective.runs) acts.push(`<button type="button" class="b-btn" data-rcact="retry" data-rcarg="${esc(r.entry)}">Retry</button>`);
  if (['queued', 'running', 'waiting'].includes(r.stored) && d.effective.executes) acts.push(`<button type="button" class="b-btn" data-rcact="cancel" data-rcarg="${esc(r.entry)}">Cancel</button>`);
  if (r.codeDrift && d.effective.runs) acts.push(`<button type="button" class="b-btn" data-rcact="refresh" data-rcarg="${esc(r.entry)}">Refresh sources</button>`);
  if (r.sync && ['pending', 'failed'].includes(r.sync.push)) acts.push(`<button type="button" class="b-btn" data-rcact="push" data-rcarg="${esc(r.sync.request)}">${r.sync.push === 'failed' ? 'Retry push' : 'Push now'}</button>`);
  return `<li class="rc-row" aria-labelledby="${id}"><p><a id="${id}" href="/brain/e/${encodeURIComponent(r.entry)}" data-route>` +
    `${esc(r.title || r.entry)}</a> <span class="b-meta">${esc(r.classification)}</span> — ` +
    `<span class="${TONE[r.state] || 'b-note'}">${esc(r.label)}</span>` +
    `${r.position ? ` <span class="b-note">#${r.position} in the queue</span>` : ''}</p>` +
    (r.why ? `<p class="b-note">${esc(r.why)}</p>` : '') +
    '<dl class="sd-facts">' +
    (r.reason ? `<dt>Queued for</dt><dd>${esc(r.reason)}${r.triggers.length ? ` — ${esc(r.triggers.join(', '))} changed` : ''}</dd>` : '') +
    `<dt>Cards it cites</dt><dd>${cardsHtml(r)}</dd>` +
    `<dt>Evaluated</dt><dd>${r.evaluatedAt ? `${esc(r.evaluatedAt)} (${esc(r.outcome || '')}) at application ` +
      `<code>${esc(short(r.evaluatedApp))}</code>` : 'not yet'}` +
    `${r.codeDrift ? ` <span class="b-warn">code changed since: the checkout is at <code>${esc(short(r.appNow))}</code>; ` +
      'only Refresh sources binds it</span>' : ''}</dd>` +
    (expl ? `<dt>Explanation</dt><dd>${expl.state === 'linked'
      ? `Explained now by <a href="/brain/e/${encodeURIComponent(expl.entry)}" data-route>${esc(expl.title || expl.entry)}</a>`
      : '<b>Current explanation pending</b> — none is written automatically'}</dd>` : '') +
    (r.attempts ? `<dt>Attempts</dt><dd>${r.attempts}</dd>` : '') +
    '</dl>' + (acts.length ? `<div class="sd-acts">${acts.join('')}</div>` : '') + '</li>';
}

function bodyHtml() {
  const d = rc.data;
  if (!d) return `<p class="b-note" aria-busy="true">${rc.error ? esc(rc.error) : 'Reading…'}</p>`;
  const counts = Object.entries(d.counts).map(([k, n]) => `${n} ${esc(k)}`).join(' · ');
  const tools = d.effective.runs ? '<div class="sd-acts">' +
    `<button type="button" class="b-btn${armed('rebuild') ? ' armed' : ''}" data-rcact="rebuild"${rc.acting ? ' disabled' : ''}>` +
    `${armed('rebuild') ? 'Confirm: queue every entry again' : 'Rebuild'}</button>` +
    `<button type="button" class="b-btn" data-rcact="refresh-all"${rc.acting ? ' disabled' : ''}>Refresh sources for every entry</button>` +
    '<button type="button" class="b-btn" data-rcact="read">Read again</button></div>'
    : '<div class="sd-acts"><button type="button" class="b-btn" data-rcact="read">Read again</button></div>';
  return headHtml(d) +
    (rc.actError ? `<p class="b-missing" role="alert">${esc(rc.actError)}</p>` : '') +
    (d.entries.length ? `<h3>Entries</h3><p class="b-note" role="status">${counts}</p>` +
      `<ul class="rc-list">${d.entries.map(r => rowHtml(r, d)).join('')}</ul>` : '') + tools +
    `<details class="sd-excl"><summary>How reconciliation decides</summary><ul class="sd-list">` +
    `${d.rules.map(x => `<li>${esc(x)}</li>`).join('')}</ul></details>`;
}

/* Maintenance health (task 200): `GET /api/brain/health`, a read asked only
   when a person opens it or reads it again — it runs local git reads, so it
   is not polled. It says the assessed scope and what was not inspected,
   every finding with its next action, coverage, the transition history,
   proposals apart from what was applied, and the Repair preview. Repair is
   `POST /api/brain/reconcile/action` with action `repair`, the preview's
   entries and digest: armed, then fired; it queues and launches nothing in
   its click. */
const hl = { open: false, data: null, busy: false, error: null, key: null };
const HL_TONE = { attention: 'rc-bad', incomplete: 'rc-warn', assessed: 'rc-calm', empty: 'b-note' };
const SEV = { problem: 'rc-bad', gap: 'rc-warn', coverage: 'b-note' };

async function readHealth() {
  hl.busy = true;
  repaint(true);
  try {
    const res = await fetch('/api/brain/health');
    const data = await res.json().catch(() => ({}));
    if (res.ok) { hl.data = data.health; hl.error = null; } else hl.error = data.error || `the board answered ${res.status}`;
  } catch (e) { hl.error = 'the board could not be asked'; }
  hl.busy = false;
  repaint(true);
}

async function repair() {
  const p = hl.data.repair;
  rc.acting = true;
  rc.actError = null;
  repaint(true);
  hl.key = hl.key || key('reconcile-repair');
  const res = await post('/api/brain/reconcile/action',
    { action: 'repair', request: hl.key, entries: p.entries, preview: p.digest });
  rc.acting = false;
  if (res.status !== 0) hl.key = null;
  hl.actError = res.ok ? null : res.error;
  hl.done = res.ok ? `Queued ${(res.data && res.data.entries ? res.data.entries.length : p.count)} ` +
    'for evaluation; the queue launches them on its next passes, within the budget.' : null;
  await read();
  await readHealth();
}

function nextHtml(f) {
  const n = f.next || {};
  const who = n.owner ? ` <span class="b-note">(${esc(n.owner)})</span>` : '';
  if (n.action === 'open-entry' || n.action === 'ask-agent') {
    return f.entry ? `<a href="/brain/e/${encodeURIComponent(f.entry)}" data-route>${esc(n.label)}</a>${who}` : esc(n.label);
  }
  if (n.action === 'review' && n.draft) return `<button type="button" class="b-btn" data-rcact="open" data-rcarg="${esc(n.draft)}">${esc(n.label)}</button>`;
  if (n.action === 'retry' && f.entry) return `<button type="button" class="b-btn" data-rcact="retry" data-rcarg="${esc(f.entry)}">${esc(n.label)}</button>`;
  if (n.action === 'push' && n.request) return `<button type="button" class="b-btn" data-rcact="push" data-rcarg="${esc(n.request)}">${esc(n.label)}</button>`;
  return `${esc(n.label || '')}${who}`;
}

function findingHtml(f) {
  const what = f.entry ? `<a href="/brain/e/${encodeURIComponent(f.entry)}" data-route>${esc(f.title || f.entry)}</a>`
    : f.file ? `<code>${esc(f.file)}</code>` : esc(f.section || 'the Brain');
  return `<li class="rc-row"><p><span class="${SEV[f.severity] || 'b-note'}">${esc(f.label)}</span> — ${what}</p>` +
    `<p class="b-note">${esc(f.message)}</p><p>Next: ${nextHtml(f)}</p></li>`;
}

function repairHtml(p) {
  if (!p.count) return '<p class="b-note">Repair: nothing needs repair that a refresh could reach.</p>';
  const a = p.authority;
  const word = (b) => b ? 'granted' : 'not granted';
  const access = p.access ? `<dt>What an evaluation may do</dt><dd>${esc(p.access.authority || '')}` +
    `${p.access.adapter ? ` <span class="b-note">harness ${esc(p.access.adapter)}` +
      `${p.access.model && p.access.model.model ? `, model ${esc(p.access.model.model.value || 'harness default')}` : ''}</span>` : ''}` +
    `${p.access.sources !== undefined ? ` · ${p.access.sources} work-root sources pinned` : ''}` +
    `${(p.access.blockers || []).length ? ` <span class="b-missing">${p.access.blockers.map(esc).join('; ')}</span>` : ''}</dd>` : '';
  const btn = p.available
    ? `<button type="button" class="b-btn b-primary${armed('repair') ? ' armed' : ''}" data-rcact="repair"${rc.acting ? ' disabled' : ''}>` +
      `${armed('repair') ? `Confirm: queue ${p.count}` : `Repair: queue ${p.count} affected ${p.count === 1 ? 'entry' : 'entries'}`}</button>`
    : `<p class="b-note">Repair is not available: ${esc(p.refused || '')}</p>`;
  return '<h4>Repair</h4><dl class="sd-facts">' +
    `<dt>Would</dt><dd>${esc(p.would || '')}</dd>` +
    `<dt>Entries</dt><dd>${p.entries.map(e => `<a href="/brain/e/${encodeURIComponent(e)}" data-route>${esc(e)}</a>`).join(', ')}</dd>` +
    `<dt>Authority now</dt><dd>evaluate ${word(a.evaluate)} · apply ${word(a.apply)} · explanation update ${word(a.maintain)} · ` +
    `executes here ${a.executes ? 'yes' : 'no'}</dd>` +
    `<dt>Budget</dt><dd>${p.batches} ${p.batches === 1 ? 'evaluation' : 'evaluations'}; ${p.launchesLeft} launches left today` +
    `${p.fitsToday ? '' : ' <span class="b-warn">— not all fit today</span>'}</dd>` + access +
    `</dl><p class="b-note">${esc(p.never)}</p><div class="sd-acts">${btn}</div>`;
}

function healthBody() {
  const d = hl.data;
  if (!d) return `<p class="b-note" aria-busy="${hl.busy}">${hl.error ? esc(hl.error) : 'Reading…'}</p>`;
  const s = d.scope;
  const shown = d.findings.slice(0, 40);
  const rest = d.findings.slice(40);
  const trans = (t) => `<li>${esc(t.at)} · <a href="/brain/e/${encodeURIComponent(t.entry)}" data-route>${esc(t.title || t.entry)}</a> → ` +
    `<b>${esc(t.classification)}</b> by ${esc(t.actor)} <span class="b-note">(${esc(t.by)})</span> — ${esc(t.reason)}</li>`;
  return `<p role="status"><b class="${HL_TONE[d.state] || 'b-note'}">${esc(d.label)}</b></p>` +
    (hl.actError ? `<p class="b-missing" role="alert">${esc(hl.actError)}</p>` : '') +
    (hl.done ? `<p class="rc-calm">${esc(hl.done)}</p>` : '') +
    '<dl class="sd-facts">' +
    `<dt>Assessed scope</dt><dd>${s.assessed} of ${s.entries} live entries reconciled against their current inputs; ` +
    `${s.neverAssessed} never reconciled; ${s.inputsChanged} with changed inputs; ${s.held} held by an override; ${s.retired} retired</dd>` +
    `<dt>Bindings</dt><dd>${s.bindings.checked} checked, ${s.bindings.notChecked} not checked ` +
    `<span class="b-note">(at most ${s.bindings.gitBudget} git reads)</span></dd>` +
    `<dt>Files</dt><dd>${s.files.unreadable} not readable as entries${s.files.truncated ? '; the listing was truncated' : ''}</dd>` +
    `<dt>Last assessed</dt><dd>${s.lastAssessed ? `${esc(s.lastAssessed)}${s.oldestAssessed !== s.lastAssessed ? ` (oldest ${esc(s.oldestAssessed)})` : ''}` : 'never'}</dd>` +
    `<dt>Excluded</dt><dd>${s.excluded.length ? s.excluded.map(x => `${esc(x.entry)} — ${esc(x.why)}`).join('; ') : 'none'}</dd></dl>` +
    `<details class="sd-excl"><summary>What this reading does not inspect</summary><ul class="sd-list">` +
    `${s.notInspected.map(x => `<li>${esc(x)}</li>`).join('')}<li>${esc(d.reads)}</li></ul></details>` +
    repairHtml(d.repair) +
    `<h4>Findings</h4>${shown.length ? `<ul class="rc-list">${shown.map(findingHtml).join('')}</ul>` : '<p class="b-note">None in what was inspected.</p>'}` +
    (rest.length || d.findingsOmitted ? `<details class="sd-excl"><summary>${rest.length + d.findingsOmitted} more findings</summary>` +
      `<ul class="rc-list">${rest.map(findingHtml).join('')}</ul></details>` : '') +
    `<h4>Handbook coverage</h4><ul class="sd-list">${d.coverage.map(c => `<li>${esc(c.title)}: <b>${esc(c.state)}</b> — ${esc(c.message)}</li>`).join('')}</ul>` +
    `<p class="b-note">Coverage is shown, never required: no merge or launch waits for it.</p>` +
    `<h4>Proposals and what was applied</h4><p>${d.proposals.length} waiting for a person` +
    `${d.proposals.length ? `: ${d.proposals.map(p => esc(p.title || p.entry)).join(', ')}` : ''}; ${d.applied.length} applied` +
    `${d.applied.length ? `: ${d.applied.map(p => esc(p.title || p.entry)).join(', ')}` : ''}.</p>` +
    (d.overrides.length ? `<p>Manual overrides: ${d.overrides.map(o => `<a href="/brain/e/${encodeURIComponent(o.entry)}" data-route>${esc(o.title || o.entry)}</a> — ${esc(o.override)}`).join('; ')}</p>` : '') +
    `<details class="sd-excl"><summary>Transition history (${d.transitions.length}${d.transitionsOmitted ? ` of ${d.transitions.length + d.transitionsOmitted}` : ''})</summary>` +
    `<ul class="sd-list">${d.transitions.map(trans).join('')}</ul></details>` +
    `<div class="sd-acts"><button type="button" class="b-btn" data-rcact="health-read"${hl.busy ? ' disabled' : ''}>Read health again</button></div>`;
}

function healthHtml() {
  const head = '<section class="b-panel" aria-labelledby="hl-h">' +
    '<h2 id="hl-h" tabindex="-1">Maintenance health</h2>' +
    '<p class="b-note">What the work has affected across the whole Brain: explanations whose cards, code or bindings ' +
    'moved, broken Historical and Current links, open outcomes and handbook coverage — and what was not inspected. ' +
    'Reading it changes nothing.</p>';
  if (!hl.open) {
    return head + '<div class="sd-acts"><button type="button" class="b-btn" data-rcact="health-show">' +
      'Read maintenance health</button></div></section>';
  }
  return head + healthBody() + '</section>';
}

function recHtml() {
  const head = '<section class="b-panel" aria-labelledby="rc-h">' +
    '<h2 id="rc-h" tabindex="-1">Automatic classification</h2>' +
    '<p class="b-note">Keep classifications reconciled with the work: when a card an entry cites changes, the ' +
    'entry is evaluated again with Evaluate\'s own rules. Off until you turn it on; evaluating and ' +
    'applying are separate authorities, and a manual override is never reclassified.</p>';
  if (!rc.open) {
    return head + '<div class="sd-acts"><button type="button" class="b-btn" data-rcact="show">' +
      'Show automatic classification</button></div></section>' + healthHtml();
  }
  if (!asked) { asked = true; read(); }
  return head + bodyHtml() + '</section>' + healthHtml();
}

function act(name, arg) {
  if (name === 'health-show') { hl.open = true; readHealth(); return; }
  if (name === 'health-read') { readHealth(); return; }
  if (name === 'repair') {
    if (!armed('repair')) { rc.armed = 'repair'; repaint(true); return; }
    rc.armed = null;
    repair();
    return;
  }
  if (name === 'show') { rc.open = true; asked = false; repaint(true); return; }
  if (name === 'read') { read(); return; }
  if (name === 'disarm') { rc.armed = null; repaint(true); return; }
  if (name === 'open') { openEval(arg); return; }
  if (name === 'policy') {
    if (!armed(arg)) { rc.armed = arg; repaint(true); return; }
    rc.armed = null;
    if (CHANGES[arg]) setPolicy(CHANGES[arg]);
    return;
  }
  if (name === 'rebuild') {
    if (!armed('rebuild')) { rc.armed = 'rebuild'; repaint(true); return; }
    rc.armed = null;
    action('rebuild');
    return;
  }
  if (name === 'refresh-all') { action('refresh'); return; }
  if (['retry', 'cancel', 'refresh'].includes(name)) { action(name, [arg]); return; }
  if (name === 'push') {
    post('/api/brain/retry', { request: arg, action: 'push' }).then(res => {
      if (!res.ok) rc.actError = res.error;
      read();
    });
  }
}

let wired = false;
function wireReconcile(el) {
  if (wired) return;
  wired = true;
  el.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-rcact]');
    if (!b || !el.contains(b) || b.disabled) return;
    act(b.dataset.rcact, b.dataset.rcarg);
  });
}

export { recHtml, setRecHooks, wireReconcile };
