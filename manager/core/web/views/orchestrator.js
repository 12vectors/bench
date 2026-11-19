/* The Orchestrator (task 145): a top-level view of what the Bench scope and
   its phase scopes are doing — managed work, phase scopes, decisions,
   recovery, activity and the operating agreement — with every action a
   person may take, each saying exactly what it does.

   It owns no orchestration logic. Everything it draws is what the server
   answered: `GET /api/orchestration` (the effective agreement and its
   draft, authority with provenance, capacity, eligibility, the schedule,
   delivery, phase scopes and recovery), `GET /api/orchestration/reasoning`
   and `GET /api/card/decisions` — all reads, so opening this view, or any
   section of it, launches no run, runs no check and makes no worktree.
   Every write goes through the route that owns it, which decides again:
   a disabled button here is a hint, never the rule.

   Untrusted text — a question, a reasoning summary, a finding, a reason a
   refusal quotes — is escaped on the way in (`esc`) or rendered by the
   page's own Markdown renderer, which escapes before it writes a tag.

   Imports lib/, words-free and app/state.js only: nothing else on the page
   is reached for, so the view can be read on its own. */
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { arming } from '../lib/arm.js';
import { $, esc } from '../lib/dom.js';
import { setHtml } from '../lib/keyed.js';
import { md } from '../lib/md.js';
import { ageWords, capacityWords, GROUP_WORDS, nextStep, orchHref, overall, RUN_WORDS, scopeName, SECTION_WORDS, SOURCE_WORDS, workGroup } from '../lib/orchwords.js';
import { cardId, findCard, ORCH_SECTIONS } from '../lib/route.js';
import { scheduleRender } from '../lib/schedule.js';
import { ago } from '../lib/time.js';
import { toast } from '../lib/toast.js';

// how long a reading is fresh enough not to be asked again on a repaint
const FRESH_MS = 5000;
// armed actions: one store of its own, swept by nothing but its windows
const ARMS = {};
const arms = arming({ store: ARMS });
// the header a policy route needs (see settings/policy.js): a form from
// another origin cannot set it
const POLICY_HEADERS = { 'Content-Type': 'application/json', 'X-Bench-Settings': '1' };

function orch() {
  if (!S.orch) {
    S.orch = { reading: null, schedule: null, reasoning: {}, reasoningReady: null,
               at: 0, error: null, errorAt: 0, fetching: false, again: false,
               dec: {}, forms: {}, reqs: {}, open: {}, edit: null, preview: null,
               notice: null, wired: false };
  }
  return S.orch;
}

/* ── reading ─────────────────────────────────────────────────────────── */

async function getJson(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `the board answered ${res.status}`);
  return body;
}

/* The orchestration reading and the reasoning jobs: two GETs. Asked when
   the view is drawn and the last reading is older than FRESH_MS, or after
   a write; never retried by itself beyond the next repaint. */
async function loadOrch(force) {
  const o = orch();
  if (o.fetching) { o.again = o.again || !!force; return; }
  if (!force && o.at && Date.now() - o.at < FRESH_MS && !o.error) return;
  if (!force && o.error && Date.now() - o.errorAt < FRESH_MS) return;
  o.fetching = true;
  try {
    const [main, why] = await Promise.all([
      getJson('/api/orchestration'),
      getJson('/api/orchestration/reasoning').catch(() => null)]);
    o.reading = main.orchestration || null;
    o.schedule = main.schedule || null;
    if (why) { o.reasoning = why.reasoning || {}; o.reasoningReady = why.readiness || null; }
    o.error = null;
    o.at = Date.now();
  } catch (e) {
    o.error = (e && e.message) || 'the board could not be asked';
    o.errorAt = Date.now();
  } finally {
    o.fetching = false;
  }
  scheduleRender();
  if (o.again) { o.again = false; loadOrch(true); }
}

/* One card's whole orchestration history, read when it is shown, and again
   when the board's summary of it moves. */
async function loadDecisions(task, force) {
  const o = orch();
  const sum = JSON.stringify(((S.state && S.state.decisions) || {})[task.file] || null);
  const key = `${task.stage}:${task.rev || ''}:${sum}`;
  const have = o.dec[task.file];
  if (have && (have.fetching || (!force && have.key === key))) return;
  o.dec[task.file] = { ...(have || {}), key, fetching: true };
  try {
    const data = await getJson(`/api/card/decisions?file=${encodeURIComponent(task.file)}` +
                               `&stage=${encodeURIComponent(task.stage)}`);
    o.dec[task.file] = { key, data, error: null, at: Date.now() };
  } catch (e) {
    o.dec[task.file] = { ...(have || {}), key, error: (e && e.message) || 'not read',
                         fetching: false };
  }
  scheduleRender();
}

/* ── small readings ──────────────────────────────────────────────────── */

function tasks() { return ((S.state && S.state.board && S.state.board.stages) || []).flatMap(s => s.tasks || []); }
function taskByFile(file) { return tasks().find(t => t.file === file) || null; }
function title(t) { return t ? String(t.title || t.file).replace(/^#?\d+\s*[—–-]\s*/, '') : ''; }
function idOf(t) { return t ? cardId(t, S.state) : null; }
function cardLink(t, text) {
  if (!t) return esc(text || '');
  return `<a data-route data-o="card-${esc(t.file)}" href="/cards/${esc(encodeURIComponent(idOf(t)))}">` +
    `${esc(text || ('#' + (t.number || t.file)))}</a>`;
}
function num(t) { return t && t.number ? `#${parseInt(t.number, 10)}` : (t ? t.file : ''); }
function scopeOf(sid) {
  const r = orch().reading;
  return r ? (r.effective.scopes || []).find(s => s.id === sid) || null : null;
}
function runningAgentOn(file) {
  return ((S.state && S.state.agents) || []).find(a => a.task === file && a.status === 'running') || null;
}
function openDecisionCount() {
  return Object.values((S.state && S.state.decisions) || {})
    .reduce((n, d) => n + (d.open || 0), 0);
}
function badge(text, tone) {
  return `<span class="o-badge ${esc(tone || 'neutral')}">${esc(text)}</span>`;
}
function when(iso) {
  if (!iso) return '';
  const t = typeof iso === 'number' ? iso : Date.parse(iso) / 1000;
  return Number.isNaN(t) ? esc(String(iso)) : `${esc(ago(t))} ago`;
}
function selected() {
  const id = S.orchItem;
  if (!id) return null;
  const found = findCard(id, S.state);
  return found.task || null;
}

function facts() {
  const o = orch();
  const r = o.reading, sch = o.schedule || {};
  const summary = (S.state && S.state.orchestration) || {};
  const ops = (r && r.operations) || sch.operations || { counts: {} };
  const rec = sch.recovery || {};
  const inst = r ? r.installation : null;
  const status = r ? r.status : summary.status;
  const managedFiles = r ? r.cards.filter(c => c.scope).map(c => c.card)
    : Object.entries(summary.cards || {}).filter(([, c]) => c.scope).map(([f]) => f);
  return {
    offline: !!(o.error && o.errorAt >= o.at) || !!S.stateError,
    status: status || 'off',
    configured: r ? r.effective.configured : !!summary.configured,
    unknownOps: (ops.counts || {}).unknown || 0,
    breakerOpen: !!((rec.breaker || sch.breaker || {}).open),
    heldIncidents: (rec.held || []).length,
    openDecisions: openDecisionCount(),
    adoptionRequired: !!(inst && inst.adoptionRequired && status === 'enabled'),
    replica: !!(inst && status === 'enabled' && !inst.executes),
    reasoning: Object.values(o.reasoning || {}).some(j => j.status === 'running' || j.status === 'queued'),
    launching: ((ops.counts || {}).intent || 0) > 0,
    working: managedFiles.some(f => runningAgentOn(f)),
    managed: managedFiles.length,
  };
}

/* ── actions ─────────────────────────────────────────────────────────── */

async function send(url, body, policy) {
  let res;
  try {
    res = await fetch(url, { method: 'POST',
      headers: policy ? POLICY_HEADERS : { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}) });
  } catch (e) {
    return { ok: false, status: 0, data: {}, error: 'the board could not be asked — nothing is known to have happened' };
  }
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data,
           error: res.ok ? null : (data && data.error) || `the board answered ${res.status}` };
}

function newRequest(prefix) {
  const bytes = new Uint8Array(8);
  (globalThis.crypto || { getRandomValues: (a) => a.map(() => Math.floor(Math.random() * 256)) })
    .getRandomValues(bytes);
  return `${prefix}-` + [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
}

/* An armed action: the first click arms (the button says what it will do),
   the second fires it once. A refusal is said in the notice and a toast;
   the reading is asked again either way. */
function armed(key, run) {
  const step = arms.click(key, undefined, scheduleRender);
  if (step === 'busy') return;
  if (step === 'armed') { paintNow(); return; }
  arms.fire(key, () => {
    toast('no answer — it was not sent again; read the board to see what happened', true);
    scheduleRender();
  });
  paintNow();
  Promise.resolve().then(run).catch((e) => ({ ok: false, error: String(e && e.message || e) }))
    .then((res) => {
      arms.settle(key);
      if (res && res.ok === false) {
        orch().notice = { key, tone: 'alarm', text: res.error || 'refused' };
        toast(res.error || 'refused', true);
      } else if (res && res.said) {
        orch().notice = { key, tone: 'calm', text: res.said };
        toast(res.said);
      }
      loadOrch(true);
      paintNow();
    });
}

function btn(key, label, opts = {}) {
  const phase = arms.phase(key);
  const text = phase === 'armed' ? (opts.confirm || `Confirm: ${label}`)
    : phase === 'busy' ? `${label}…` : label;
  const cls = ['o-btn', opts.primary ? 'primary' : '', opts.danger ? 'danger' : '',
               phase === 'armed' ? 'armed' : '', phase === 'busy' ? 'busy' : ''].filter(Boolean).join(' ');
  const data = Object.entries(opts.data || {}).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('');
  const off = opts.disabled || phase === 'busy' || facts().offline;
  return `<button type="button" class="${cls}" data-oact="${esc(opts.act || key)}" data-o="${esc(key)}"` +
    `${data}${off ? ' disabled' : ''}${opts.why ? ` title="${esc(opts.why)}"` : ''}>${esc(text)}</button>`;
}

/* What every action does, said once and shown beside its button. */
const EFFECTS = {
  pauseBench: 'Stops new launches, check requests, moves and merges in every scope, phase scopes included. Runs already working continue; their endings are observed and recorded. Nothing is cancelled.',
  resumeBench: 'New work may start again under the agreement in force. Every action is authorized again immediately before it happens; finished runs are recognised, never relaunched, and unknown outcomes stay held.',
  pausePhase: 'Stops new starts and integration in this phase only. Standalone work and other phases continue; runs already working continue.',
  resumePhase: 'This phase may schedule again — unless the Bench scope is paused, which still reaches it.',
  handback: 'You drive this card: the orchestrator will not launch, move or merge it. Its branch, worktree and history are kept; a run already working continues unless you cancel it.',
  reclaim: 'The orchestrator may act on this card again, if it is still yours by its last move and the agreement grants the step. Your changes are read as they are; nothing is reset.',
  exclude: 'Leaves this card out of its scope entirely: nothing is scheduled for it. Branch, worktree and runs are untouched.',
  include: 'Puts this card back in its scope; it is scheduled only if its last move is yours.',
  cancel: 'Stops this named run (‖ hold) and the checks it asked for, recorded as cancelled. Branch, worktree and history are kept. No other card, run or scope is affected, nothing is paused, and nothing retries by itself — the card waits for you. Stopping is confirmed only when the process is observed gone.',
  adoptCard: 'Marks this working card adopted by this installation after a handover, so automation may act on it from here. Its branch, worktree and runs are left as they are.',
  designate: 'Makes this installation the one that executes. It launches nothing now; with an enabled agreement the scheduler’s next pass may start eligible work. Another installation’s designation is never taken over.',
  handover: 'Releases execution so another installation can adopt it. Refused while any run is working or any operation is unsettled; nothing is stopped to make it pass.',
  adoptPhase: 'Moves this phase from the phase runner to its phase scope. Refused while anything still works it. Writes one line to the phase log; no branch, worktree or member is touched, and an integrated member is never run again.',
  releasePhase: 'Hands this phase back to the phase runner. Refused while anything works it. The runner does not resume until a person runs it.',
  launched: 'Records that the run was in fact launched: its start is spent and it reads as ended. Nothing is launched.',
  notLaunched: 'Records that nothing ran: the slot is freed and a new attempt may follow on its own merits. Nothing is launched.',
  release: 'Releases a refused launch so it may be tried again on the same brief. Nothing is launched now.',
  resolveIncident: 'Marks the incident handled. It launches, stops and retries nothing, and resets no other incident’s attempts.',
  resetBreaker: 'Closes the circuit breaker, once the cause (an outage, a credential, the harness) is fixed. Nothing is launched by closing it.',
  answer: 'Records your answer on the card, bound to the question and brief you read. It grants no command, credential, budget or authority; the next step is decided again from the card.',
  withdraw: 'Withdraws the question — it no longer needs an answer. The question, its options and history stay on the card.',
  apply: 'Puts this draft in force: one file is written. It starts, moves, merges and cancels nothing by itself; every later action is authorized against it, read again before each one. Runs keep the grants frozen at their launch. There is no shadow stage.',
  discard: 'Deletes the draft. The agreement in force is unchanged.',
  saveDraft: 'Saves the draft. Saving is not applying: nothing that runs changes.',
};

/* ── the shell ───────────────────────────────────────────────────────── */

function navHtml(section) {
  const r = orch().reading;
  const f = facts();
  const marks = {
    decisions: f.openDecisions ? ` <span class="o-count">${f.openDecisions}</span>` : '',
    recovery: f.heldIncidents || f.unknownOps || f.breakerOpen ? ' <span class="o-mark" aria-label="needs you"></span>' : '',
    agreement: r && r.draft && r.draft.differs ? ' <span class="o-count">draft</span>' : '',
  };
  return `<nav class="o-nav" aria-label="Orchestrator sections">` +
    ORCH_SECTIONS.map(s => `<a data-route data-o="nav-${s}" href="${esc(orchHref(s))}"` +
      `${s === section ? ' aria-current="page" class="on"' : ''}>${esc(SECTION_WORDS[s])}${marks[s] || ''}</a>`).join('') +
    `</nav>`;
}

function stripHtml() {
  const f = facts();
  const st = overall(f);
  const next = nextStep(st.word);
  const r = orch().reading;
  const nextHtml = next.action === 'reload'
    ? btn('reload', next.label, { act: 'reload', primary: true })
    : next.href ? `<a class="o-btn primary" data-route data-o="next" href="${esc(next.href)}">${esc(next.label)}</a>`
      : `<a class="o-btn primary" data-route data-o="next" href="${esc(orchHref(next.section))}">${esc(next.label)}</a>`;
  const bench = scopeOf('bench');
  const pause = !r || !r.effective.configured || !r.effective.valid ? ''
    : bench && bench.paused ? btn('resume:bench', 'Resume all new work', { act: 'scope', data: { scope: 'bench', action: 'resume' } })
      : btn('pause:bench', 'Pause all new work', { act: 'scope', data: { scope: 'bench', action: 'pause' } });
  const at = orch().at ? `read ${ago(orch().at / 1000)} ago` : 'not read yet';
  return `<section class="o-status ${esc(st.tone)}" aria-labelledby="o-status-h">` +
    `<div class="o-statustext"><p class="o-eyebrow">Orchestrator · ${esc(at)}</p>` +
    `<h2 id="o-status-h">${badge(st.label, st.tone)}</h2>` +
    `<p>${esc(st.sentence)}</p>` +
    (r ? `<p class="o-dim">Agreement ${esc(r.effective.revision)}` +
      ` · ${esc(identityWords(r.identity))}</p>` : '') +
    `</div><div class="o-acts">${nextHtml}${pause}</div>` +
    (pause ? `<p class="o-effect">${esc(bench && bench.paused ? EFFECTS.resumeBench : EFFECTS.pauseBench)}</p>` : '') +
    `</section>`;
}

function identityWords(id) {
  if (!id || !id.name) return 'this board has no Git identity, so no card is yours by its last move';
  return `you are ${id.name} (from ${id.source || 'git'}); only cards whose latest stage move was yours are eligible — attribution is Git’s, not authentication`;
}

function noticeHtml() {
  const n = orch().notice;
  if (!n) return '';
  return `<p class="o-notice ${esc(n.tone)}" role="status">${esc(n.text)}` +
    ` <button type="button" class="o-link" data-oact="dismiss" data-o="dismiss">Dismiss</button></p>`;
}

function offlineHtml() {
  const o = orch();
  if (!facts().offline) return '';
  return `<div class="o-offline" role="alert"><strong>Offline — the last reading is not current truth.</strong> ` +
    `${esc(o.error || (S.stateError && S.stateError.why) || 'the board did not answer')}. ` +
    `Runs on the server may still be alive; a disconnected page is no evidence they stopped. ` +
    `Every action here is disabled until the board answers.</div>`;
}

function sectionHtml(section) {
  const o = orch();
  if (!o.reading) {
    return o.error ? `<p class="o-empty">The orchestration reading could not be taken: ${esc(o.error)}.</p>`
      : `<p class="o-empty" aria-busy="true">Reading the orchestration…</p>`;
  }
  switch (section) {
    case 'overview': return overviewHtml();
    case 'work': return workHtml();
    case 'decisions': return decisionsHtml();
    case 'phases': return phasesHtml();
    case 'recovery': return recoveryHtml();
    case 'activity': return activityHtml();
    case 'agreement': return agreementHtml();
    default:
      return `<p class="o-empty" role="alert">The Orchestrator has no section called “${esc(section)}”. ` +
        `<a data-route href="/orchestrator">Open its Overview</a>.</p>`;
  }
}

function shellHtml() {
  const section = S.orchSection || 'overview';
  return navHtml(section) +
    `<div class="o-main"><h1 class="o-title">${esc(SECTION_WORDS[section] || 'Orchestrator')}</h1>` +
    offlineHtml() + stripHtml() + noticeHtml() + sectionHtml(section) + `</div>`;
}

/* ── overview ────────────────────────────────────────────────────────── */

function authorityHtml(scope) {
  return `<ul class="o-auth">` + Object.entries(scope.authority || {}).map(([word, a]) =>
    `<li class="${a.allowed ? 'yes' : 'no'}"><span class="o-glyph" aria-hidden="true">${a.allowed ? '✓' : '✕'}</span>` +
    `${esc(word)} <span class="o-dim">${a.allowed ? 'granted' : 'not granted'}` +
    `${a.by && a.by !== scope.id ? ` · decided by ${esc(scopeName(a.by))}` : ''}</span></li>`).join('') +
    `</ul>`;
}

function capacityHtml(scope) {
  const r = orch().reading;
  const usage = (r && r.usage) || {};
  const mine = (usage.running || {})[scope.id] || {};
  const rows = ['implementation', 'review', 'reasoning'].map(pool => {
    const cap = scope.capacity[pool] || {};
    const used = scope.kind === 'bench' ? (usage.totals || {})[pool] || 0 : mine[pool] || 0;
    const total = scope.kind === 'bench' ? cap.total : cap.own;
    const extra = scope.kind === 'bench' && cap.allocated
      ? ` · ${cap.allocated} allocated to phases, ${cap.own} kept` : scope.kind === 'phase' ? ' · allocated from the Bench total' : '';
    const note = pool === 'reasoning' && !total ? ' · reasoning and recovery take implementation slots' : '';
    return `<div><dt>${esc(pool)} slots</dt><dd>${esc(capacityWords(used, total))}${esc(extra)}${esc(note)}</dd></div>`;
  });
  const starts = scope.kind === 'bench' ? usage.startsTodayTotal || 0 : (usage.startsToday || {})[scope.id] || 0;
  const daily = scope.capacity.dailyStarts || {};
  rows.push(`<div><dt>launches today</dt><dd>${esc(`${starts} of ${scope.kind === 'bench' ? daily.total : daily.own}`)}` +
    ` <span class="o-dim">launch counts, not a bill</span></dd></div>`);
  const ceil = Object.entries(scope.ceilings || {}).map(([k, c]) =>
    `${k} ${c.value}${c.by !== scope.id ? ` (from ${scopeName(c.by)})` : ''}`).join(' · ');
  rows.push(`<div><dt>ceilings</dt><dd>${esc(ceil)}</dd></div>`);
  return `<dl class="o-kv">${rows.join('')}</dl>`;
}

function scopeStateWords(scope) {
  if (scope.off === 'invalid') return ['Off — invalid agreement', 'alarm'];
  if (scope.off === 'parent-disabled') return ['Off — the Bench scope is off', 'idle'];
  if (scope.off) return ['Off', 'idle'];
  if (scope.pausedBy) return [scope.pausedBy === scope.id ? 'Paused' : `Paused by ${scopeName(scope.pausedBy)}`, 'alarm'];
  return ['Active', 'calm'];
}

function scopeCardHtml(scope) {
  const [word, tone] = scopeStateWords(scope);
  const phaseTask = scope.card ? tasks().find(t => t.isPhase && String(parseInt(t.number, 10)) === String(scope.card)) : null;
  const head = scope.kind === 'bench' ? 'Bench scope — standalone cards and phase scopes'
    : `Phase #${scope.card}${phaseTask ? ' · ' + title(phaseTask) : ''} — its members only`;
  const link = scope.kind === 'phase'
    ? `<a data-route href="${esc(orchHref('phases', scope.card))}">Open this phase scope</a>` +
      (phaseTask ? ` · ${cardLink(phaseTask, 'phase card')}` : '')
    : `<a data-route href="${esc(orchHref('work'))}">Its managed work</a>`;
  return `<article class="o-panel o-scope" aria-label="${esc(head)}">` +
    `<h3>${esc(head)} ${badge(word, tone)}</h3>` +
    (scope.kind === 'phase' ? `<p class="o-dim">Strategy ${esc(scope.strategy || 'sequential')}. Authority is the intersection with the Bench scope; capacity is allocated from its total, never duplicated.</p>` : '') +
    `<details class="o-fold" data-okeep="auth-${esc(scope.id)}"${keepOpen('auth-' + scope.id)}><summary>Authority</summary>${authorityHtml(scope)}</details>` +
    capacityHtml(scope) + `<p>${link}</p></article>`;
}

function mergeHtml() {
  const r = orch().reading;
  const rules = r.mergeRules || {};
  const bench = scopeOf('bench');
  const merge = bench && bench.authority.merge && bench.authority.merge.allowed;
  const names = { member: 'Phase member into its phase branch', integration: 'Phase acceptance (integrated head)', acceptance: 'Card merge' };
  const rows = Object.entries(rules.scopes || {}).map(([k, v]) =>
    `<div><dt>${esc(names[k] || k)}</dt><dd>${v.gated
      ? `${esc(`requires ${(v.checks || []).join(', ') || 'no checks'}`)}${v.review && v.review !== 'none' ? esc(` and review (${[].concat(v.review).join(', ')})`) : ''}`
      : '<strong>No checks required by your policy</strong> — a delegated merge is <em>not verified</em>'}</dd></div>`);
  return `<article class="o-panel"><h3>Merge policy ${badge(merge ? 'merge delegated' : 'merge is yours', merge ? 'neutral' : 'idle')}</h3>` +
    `<p class="o-dim">A delegated merge waits for exactly the requirements you configured: unmet blocks, running holds. Nothing here activates a gate, invents a pass or grants an override.</p>` +
    (rows.length ? `<dl class="o-kv">${rows.join('')}</dl>` : '<p>No merge rule could be read.</p>') +
    (rules.problems && rules.problems.length ? `<p class="o-bad">${esc(rules.problems.join(' · '))}</p>` : '') +
    `</article>`;
}

function installationHtml() {
  const inst = orch().reading.installation || {};
  const role = inst.executes ? 'Executes here' : inst.designated ? 'Read-only replica' : 'Not designated';
  const tone = inst.executes ? 'calm' : 'neutral';
  const acts = [];
  if (!inst.designated || inst.state === 'released') {
    acts.push(btn('designate', 'Designate this installation', { act: 'install', data: { action: 'designate' } }) +
      `<p class="o-effect">${esc(EFFECTS.designate)}</p>`);
  } else if (inst.role === 'owner' || inst.executes) {
    acts.push(btn('handover', 'Hand execution over', { act: 'install', data: { action: 'handover' } }) +
      `<p class="o-effect">${esc(EFFECTS.handover)}</p>`);
  }
  return `<article class="o-panel"><h3>Execution installation ${badge(role, tone)}</h3>` +
    `<dl class="o-kv"><div><dt>role</dt><dd>${esc(inst.role || 'unknown')}${inst.problem ? ' — ' + esc(inst.problem) : ''}</dd></div>` +
    `<div><dt>designation</dt><dd>${esc(inst.designated ? (inst.designated === inst.thisInstallation ? 'this installation' : 'another installation') : 'none')}` +
    `${inst.since ? ' · since ' + when(inst.since) : ''}${inst.state ? ' · ' + esc(inst.state) : ''}</dd></div>` +
    `<div><dt>lock</dt><dd>${inst.lockHeld ? 'held by this process' : inst.lockFree ? 'free' : 'held elsewhere'}</dd></div>` +
    (inst.adoptionRequired ? `<div><dt>adoption</dt><dd>working cards need adopting after the handover (${esc((inst.adoptedCards || []).length)} adopted)</dd></div>` : '') +
    `</dl><p class="o-dim">One designated installation executes; a replica only reads. Git sync is not a lock, and a stale heartbeat proves nothing — there is no automatic failover.</p>` +
    acts.join('') + `</article>`;
}

function overviewHtml() {
  const r = orch().reading;
  const sch = orch().schedule || {};
  if (!r.effective.configured) {
    return `<article class="o-panel"><h3>Nothing starts by itself</h3>` +
      `<p>The orchestrator is off until you preview and apply an operating agreement. Updating Bench does not enable it, and there is no shadow stage: once you apply an enabled agreement and this installation is designated, eligible work can launch.</p>` +
      `<p><a class="o-btn primary" data-route data-o="go-agreement" href="${esc(orchHref('agreement'))}">Review the operating agreement</a></p></article>` +
      installationHtml();
  }
  const groups = {};
  const summary = (S.state && S.state.orchestration) || {};
  for (const c of r.cards) {
    const g = workGroup(c, ((summary.cards || {})[c.card] || {}).manual);
    groups[g] = (groups[g] || 0) + 1;
  }
  const rec = sch.recovery || {};
  const ops = r.operations || { counts: {} };
  const facts_ = [
    ['Managed work', `${groups.yours || 0} yours · ${groups.manual || 0} driven by you · ${groups.foreign || 0} someone else’s · ${groups.unattributed || 0} unattributed · ${groups.unmanaged || 0} outside a scope`, 'work'],
    ['Decisions', `${openDecisionCount()} open question${openDecisionCount() === 1 ? '' : 's'} on cards`, 'decisions'],
    ['Recovery', `${(rec.held || []).length} held or exhausted · ${(rec.incidents || []).length} incidents on record · breaker ${(rec.breaker || {}).open ? 'OPEN' : 'closed'}`, 'recovery'],
    ['Operations', `${(ops.counts || {}).unknown || 0} unknown outcome · ${(ops.open || []).length} open · ${(ops.recent || []).length} recent`, 'activity'],
    ['Reasoning', o_reasoningWords(), 'decisions'],
  ];
  return `<div class="o-grid">` +
    `<article class="o-panel"><h3>At a glance</h3><dl class="o-kv">` +
    facts_.map(([k, v, s]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)} · <a data-route href="${esc(orchHref(s))}">open</a></dd></div>`).join('') +
    `</dl>${(r.notes || []).map(n => `<p class="o-dim">${esc(n)}</p>`).join('')}` +
    `<p class="o-dim">${esc(r.grants || '')}</p></article>` +
    r.effective.scopes.map(scopeCardHtml).join('') +
    mergeHtml() + installationHtml() + `</div>`;
}

function o_reasoningWords() {
  const ready = orch().reasoningReady;
  const jobs = Object.values(orch().reasoning || {});
  const running = jobs.filter(j => j.status === 'running' || j.status === 'queued').length;
  const base = `${running} running · ${jobs.length} cards with a reasoning job`;
  if (!ready) return base;
  return ready.ready ? `${base} · reasoning launches can start`
    : `${base} · reasoning cannot launch here: ${(ready.blockers || []).join('; ') || 'blocked'}`;
}

/* ── work ────────────────────────────────────────────────────────────── */

function scheduleItem(file) {
  const sch = orch().schedule || {};
  return (sch.items || []).find(i => i.card === file) || null;
}
function deliveryItem(file) {
  const d = (orch().schedule || {}).delivery || {};
  return (d.items || []).find(i => i.card === file) || null;
}
function runOf(file) {
  const rec = (orch().schedule || {}).recovery || {};
  return (rec.runs || []).find(r => r.card === file) || null;
}

function workRowHtml(c, manual) {
  const t = taskByFile(c.card);
  const el = c.eligibility || {};
  const item = scheduleItem(c.card);
  const del = deliveryItem(c.card);
  const agent = runningAgentOn(c.card);
  const id = idOf(t) || c.number;
  const sel = S.orchItem && t && findCard(S.orchItem, S.state).task === t;
  const step = del ? `${del.state || ''}${del.label ? ' — ' + del.label : ''}` : item ? `${item.state}` : '';
  return `<li class="o-row${sel ? ' sel' : ''}">` +
    `<a class="o-rowlink" data-route data-o="row-${esc(c.card)}" href="${esc(orchHref('work', id))}"` +
    `${sel ? ' aria-current="true"' : ''}>${esc(num(t) || c.card)} ${esc(title(t))}</a>` +
    `<span class="o-dim">${esc(c.stage)} · ${esc(scopeName(c.scope))}${manual ? ' · ' + esc(manual === 'handback' ? 'handed back' : 'excluded') : ''}</span>` +
    `<span>${el.eligible ? badge('eligible', 'calm') : badge('not eligible', 'idle')} ${esc(el.reason || c.why || '')}</span>` +
    (step ? `<span>${badge(step.split(' — ')[0] || 'step', 'neutral')} ${esc((item && item.reason) || (del && del.reason) || '')}</span>` : '') +
    (agent ? `<span>${badge('run working', 'working')} ${esc(agent.name || agent.id)}${agent.mode ? ' · ' + esc(agent.mode) : ''}</span>` : '') +
    `</li>`;
}

function workHtml() {
  const r = orch().reading;
  if (!r.effective.configured) return `<p class="o-empty">No agreement is in force, so no card is managed. <a data-route href="${esc(orchHref('agreement'))}">Operating agreement</a></p>`;
  const summary = (S.state && S.state.orchestration) || {};
  const groups = { yours: [], manual: [], foreign: [], unattributed: [], unmanaged: [] };
  for (const c of r.cards) {
    const manual = ((summary.cards || {})[c.card] || {}).manual;
    groups[workGroup(c, manual)].push(workRowHtml(c, manual));
  }
  const t = selected();
  const lists = Object.entries(groups).map(([g, rows]) =>
    `<section class="o-panel" aria-label="${esc(GROUP_WORDS[g].label)}"><h3>${esc(GROUP_WORDS[g].label)} <span class="o-count">${rows.length}</span></h3>` +
    `<p class="o-dim">${esc(GROUP_WORDS[g].help)}</p>` +
    (rows.length ? `<ul class="o-rows">${rows.join('')}</ul>` : '<p class="o-dim">None.</p>') + `</section>`).join('');
  const empty = !r.cards.length
    ? `<p class="o-empty">Nothing committed. Move a card into To Do to commit it — nothing is pulled from Backlog.</p>` : '';
  return empty + (t ? cardDetailHtml(t) : (S.orchItem ? `<p class="o-empty" role="alert">No card #${esc(S.orchItem)} on this board.</p>` : '')) + lists;
}

function signalsHtml(run) {
  if (!run) return `<p class="o-dim">No run of this card is being observed now.</p>`;
  const s = run.signals || {};
  const row = (k, v, src) => `<div><dt>${esc(k)}</dt><dd>${esc(v)} <span class="o-src">${esc(src)}</span></dd></div>`;
  const clock = (x) => x && typeof x === 'object' ? (x.ageSeconds !== undefined ? ageWords(x.ageSeconds) + ' ago' : (x.at ? String(x.at) : JSON.stringify(x))) : (x === null || x === undefined ? 'none' : String(x));
  return `<p>${badge(run.reading || 'observed', run.reading === 'stalled' ? 'alarm' : 'neutral')} ${esc(RUN_WORDS[run.reading] || '')}</p>` +
    `<dl class="o-kv">` +
    row('process', s.process || 'unknown', 'observed from the process handle') +
    row('run age', ageWords(s.ageSeconds), `observed · warning at ${run.thresholdMinutes || '?'}m, never a kill`) +
    row('last progress update', clock(s.lastProgress), 'agent-reported') +
    row('last tool activity', clock(s.lastTool), 'observed tool event') +
    row('tool call in flight', s.toolInFlight ? 'yes' : 'no', 'observed') +
    row('bench check running', s.checksRunning ? 'yes' : 'no', 'Bench') +
    row('last commit', clock(s.lastCommit), 'git') +
    `</dl><p class="o-dim">Separate signals: silence alone does not prove a stall, and a progress claim is not a check.</p>`;
}

function cardDetailHtml(t) {
  const r = orch().reading;
  const c = r.cards.find(x => x.card === t.file);
  const summary = ((S.state && S.state.orchestration) || {}).cards || {};
  const manual = (summary[t.file] || {}).manual;
  const el = (c && c.eligibility) || {};
  const mv = el.movement || {};
  const item = scheduleItem(t.file);
  const del = deliveryItem(t.file);
  const agent = runningAgentOn(t.file);
  const job = (orch().reasoning || {})[t.file];
  const usage = r.usage || {};
  const launches = (usage.launchesPerCard || {})[t.file] ?? (usage.launchesPerCard || {})[String(parseInt(t.number, 10))] ?? 0;
  const sid = c && c.scope;
  const scope = sid ? scopeOf(sid) : null;
  const n = t.number ? String(parseInt(t.number, 10)) : null;
  const acts = [];
  if (sid && n && r.effective.valid) {
    acts.push(manual === 'handback'
      ? action(`reclaim:${n}`, 'Reclaim for the orchestrator', 'scope', { scope: sid, action: 'reclaim', card: n }, EFFECTS.reclaim)
      : action(`handback:${n}`, 'Take over manually', 'scope', { scope: sid, action: 'handback', card: n }, EFFECTS.handback));
    acts.push(manual === 'excluded'
      ? action(`include:${n}`, 'Include in its scope', 'scope', { scope: sid, action: 'include', card: n }, EFFECTS.include)
      : action(`exclude:${n}`, 'Exclude from its scope', 'scope', { scope: sid, action: 'exclude', card: n }, EFFECTS.exclude));
  }
  if (agent) acts.push(action(`cancel:${agent.id}`, `Cancel ${agent.name || 'this run'}…`, 'cancel', { agent: agent.id }, EFFECTS.cancel, true));
  const inst = r.installation || {};
  if (inst.adoptionRequired && n && t.stage !== 'to-do' && !(inst.adoptedCards || []).includes(n)) {
    acts.push(action(`adopt:${n}`, 'Adopt this working card', 'install', { action: 'adopt-card', card: n }, EFFECTS.adoptCard));
  }
  return `<article class="o-panel o-detail" aria-label="${esc(num(t))} in the Orchestrator">` +
    `<h3>${esc(num(t))} · ${esc(title(t))} ${cardLink(t, 'open card')}</h3>` +
    `<dl class="o-kv">` +
    `<div><dt>stage</dt><dd>${esc(t.stage)}${t.assignee ? ' · held by ' + esc(t.assignee) : ''}</dd></div>` +
    `<div><dt>scope</dt><dd>${esc(scopeName(sid))}${c && c.why ? ' — ' + esc(c.why) : ''}${manual ? ' · ' + esc(manual === 'handback' ? 'handed back to you' : 'excluded') : ''}` +
    `${scope ? ' · ' + esc(scopeStateWords(scope)[0]) : ''}</dd></div>` +
    `<div><dt>eligibility</dt><dd>${el.eligible ? badge('eligible', 'calm') : badge('not eligible', 'idle')} ${esc(el.code || '')} — ${esc(el.reason || '')}` +
    `${el.remedy ? `<br><span class="o-dim">${esc(el.remedy)}</span>` : ''}</dd></div>` +
    `<div><dt>last move</dt><dd>${mv && (mv.mover || mv.to) ? esc(`${mv.mover || 'unknown'} → ${mv.to || '?'}${mv.kind ? ' (' + mv.kind + ')' : ''}`) + (mv.at ? ' · ' + when(mv.at) : '') : 'not recorded'}` +
    ` <span class="o-src">${esc(el.attribution || 'Git attribution, not authentication')}</span></dd></div>` +
    `<div><dt>next step</dt><dd>${item ? `${badge(item.state, item.state === 'blocked' ? 'alarm' : 'neutral')} ${esc(item.code || '')} — ${esc(item.reason || '')}` : del ? '' : 'not scheduled'}` +
    `${del ? `${badge(del.state || 'delivery', 'neutral')} ${esc(del.code || '')} — ${esc(del.reason || '')}${del.label ? `<br><span class="o-dim">${esc(del.label)}</span>` : ''}${del.head ? ` <span class="o-src">head ${esc(String(del.head).slice(0, 12))}</span>` : ''}` : ''}</dd></div>` +
    `<div><dt>launches</dt><dd>${esc(`${launches} of ${scope ? scope.ceilings.launchesPerCard.value : '?'} for this card`)} <span class="o-src">ledger</span></dd></div>` +
    `<div><dt>reasoning</dt><dd>${job ? `${esc(job.status)} (${esc(job.need)}) <span class="o-src">agent-reported when valid</span>` : 'none'}</dd></div>` +
    `</dl><h4>Run signals</h4>${signalsHtml(runOf(t.file))}` +
    (acts.length ? `<h4>Actions</h4><div class="o-actions">${acts.join('')}</div>` : '') +
    `<p><a data-route href="${esc(orchHref('decisions', idOf(t)))}">Decisions and history for this card</a></p>` +
    `</article>`;
}

function action(key, label, act, data, effect, danger, why) {
  return `<div class="o-action">${btn(key, label, { act, data, danger, disabled: !!why, why })}` +
    `<p class="o-effect">${esc(effect)}${why ? ` <strong>Not available: ${esc(why)}</strong>` : ''}</p></div>`;
}

/* ── decisions ───────────────────────────────────────────────────────── */

function decisionCards() {
  const dec = (S.state && S.state.decisions) || {};
  return Object.entries(dec).map(([file, d]) => ({ t: taskByFile(file), d }))
    .filter(x => x.t)
    .sort((a, b) => (b.d.open || 0) - (a.d.open || 0) || (b.d.reevaluate || 0) - (a.d.reevaluate || 0) ||
      String(a.t.number).localeCompare(String(b.t.number), undefined, { numeric: true }));
}

function decisionsHtml() {
  const list = decisionCards();
  const explicit = selected();
  const t = explicit || (list.find(x => x.d.open) || list[0] || {}).t || null;
  const rows = list.map(({ t: c, d }) => {
    const on = t && c.file === t.file;
    return `<li class="o-row${on ? ' sel' : ''}"><a class="o-rowlink" data-route data-o="dec-${esc(c.file)}" href="${esc(orchHref('decisions', idOf(c)))}"` +
      `${on ? ' aria-current="true"' : ''}>${esc(num(c))} ${esc(title(c))}</a>` +
      `<span>${d.open ? badge(`${d.open} open`, 'alarm') : badge('none open', 'idle')}` +
      `${d.reevaluate ? ' ' + badge(`${d.reevaluate} to re-evaluate`, 'neutral') : ''}` +
      ` <span class="o-dim">${d.answered || 0} answered · ${d.responses || 0} responses${d.openBlockers ? ` · ${d.openBlockers} open blockers` : ''}</span></span></li>`;
  });
  const listHtml = `<section class="o-panel o-list" aria-label="Cards with decisions"><h3>Cards with decisions</h3>` +
    (rows.length ? `<ul class="o-rows">${rows.join('')}</ul>`
      : `<p class="o-dim">No card has an orchestration question or response. Questions are drafted by a launched coding agent and kept on the card they are about.</p>`) +
    `</section>`;
  if (explicit === null && S.orchItem) {
    return `<p class="o-empty" role="alert">No card #${esc(S.orchItem)} on this board.</p>` + listHtml;
  }
  return `<div class="o-split">${listHtml}<div>${t ? decisionDetailHtml(t) : ''}</div></div>`;
}

function decisionDetailHtml(t) {
  const o = orch();
  loadDecisions(t);
  const entry = o.dec[t.file] || {};
  const d = entry.data;
  if (!d) {
    return `<article class="o-panel"><h3>${esc(num(t))} · ${esc(title(t))}</h3>` +
      (entry.error ? `<p class="o-bad" role="alert">Its history could not be read: ${esc(entry.error)}</p>` : `<p aria-busy="true">Reading the card’s history…</p>`) + `</article>`;
  }
  const open = d.questions.filter(q => q.state === 'open');
  const closed = d.questions.filter(q => q.state !== 'open');
  return `<article class="o-panel o-detail" aria-label="Decisions on ${esc(num(t))}">` +
    `<h3>${esc(num(t))} · ${esc(title(t))} ${cardLink(t, 'open card')}</h3>` +
    `<p class="o-dim">Everything here is read from the card’s own Orchestration record — the record a teammate, a restarted board or a replica reads too. ` +
    `You are ${esc((d.actor || {}).name || 'unknown')} (${esc((d.actor || {}).source || '?')}).` +
    `${d.persistence && !d.persistence.commits ? ' Answers are written to the card file and not committed (BOARD_COMMIT_MOVES is off).' : ''}</p>` +
    (d.phase ? `<p>Member of phase ${esc('#' + (d.phase.number || d.phase.file))}.</p>` : '') +
    (open.length ? open.map(q => questionHtml(t, d, q)).join('') : `<p class="o-dim">No open question on this card.</p>`) +
    aggregatedHtml(d) +
    (closed.length ? `<details class="o-fold" data-okeep="closed-${esc(t.file)}"${keepOpen('closed-' + t.file)}><summary>Resolved history (${closed.length})</summary>` +
      closed.map(q => closedQuestionHtml(q)).join('') + `</details>` : '') +
    responsesHtml(t, d) + recordHtml(t, d) +
    (d.malformed || d.misplaced ? `<p class="o-dim">${esc(`${d.malformed} malformed and ${d.misplaced} misplaced record lines were skipped — never read as records.`)}</p>` : '') +
    `</article>`;
}

function refsHtml(refs) {
  if (!refs || !refs.length) return '';
  return `<p class="o-refs">Evidence: ${refs.map(r => {
    const m = /^card:(.+)$/.exec(r);
    if (m) {
      const found = findCard(String(parseInt(m[1], 10) || m[1]), S.state).task;
      return found ? cardLink(found, r) : `<code>${esc(r)}</code>`;
    }
    return `<code>${esc(r)}</code>`;
  }).join(' · ')}</p>`;
}

function questionHtml(t, d, q) {
  const o = orch();
  const form = o.forms[q.id] || (o.forms[q.id] = { choice: null, note: '' });
  const options = q.options || [];
  const foreign = q.owner && d.actor && q.owner !== d.actor.name;
  const stuck = q.needsReevaluation;
  const key = `answer:${t.file}:${q.id}`;
  const wkey = `withdraw:${t.file}:${q.id}`;
  return `<section class="o-q" aria-labelledby="q-${esc(q.id)}">` +
    `<p class="o-eyebrow">${esc(num(t))} · question ${esc(q.id)} · asked ${when(q.asked)} · ${esc(SOURCE_WORDS[q.source] || q.source || '')}` +
    `${q.briefRev ? ` · brief ${esc(q.briefRev)}` : ''}${q.owner ? ` · addressed to ${esc(q.owner)}` : ''}</p>` +
    `<h4 id="q-${esc(q.id)}">${esc(q.text || '')}</h4>` +
    (q.why ? `<div class="o-md">${md(q.why, { base: 5 })}</div>` : '') +
    (q.recommendation ? `<p class="o-rec"><strong>Recommended:</strong> ${esc(q.recommendation)}</p>` : '') +
    refsHtml(q.evidence) +
    (stuck ? `<p class="o-bad" role="alert">The brief changed after this question was asked, so it needs re-evaluation before anyone answers it. An answer now would be refused.</p>` : '') +
    (foreign ? `<p class="o-bad">Addressed to ${esc(q.owner)}: you may read it, not answer it.</p>` : '') +
    `<fieldset class="o-options"><legend>Your answer</legend>` +
    options.map((opt, i) => `<label class="o-option${form.choice === i ? ' on' : ''}">` +
      `<input type="radio" name="q-${esc(q.id)}" value="${i}" data-ofield="choice" data-q="${esc(q.id)}" data-o="opt-${esc(q.id)}-${i}"${form.choice === i ? ' checked' : ''}>` +
      `<span>${esc(opt)}${q.recommendation && opt === q.recommendation ? ' <span class="o-recword">Recommended</span>' : ''}</span></label>`).join('') +
    `<label class="o-notefield">Note${options.length ? ' (optional with an option; required without one)' : ' (required)'}` +
    `<textarea rows="3" maxlength="2000" data-ofield="note" data-q="${esc(q.id)}" data-o="note-${esc(q.id)}">${esc(form.note)}</textarea></label>` +
    `</fieldset>` +
    `<div class="o-actions">` +
    action(key, 'Submit answer', 'answer', { file: t.file, q: q.id }, EFFECTS.answer, false,
      stuck ? 'the question needs re-evaluation first' : foreign ? `it is addressed to ${q.owner}` : '') +
    action(wkey, 'Withdraw question', 'withdraw', { file: t.file, q: q.id },
      EFFECTS.withdraw + ' The note above is required and says why.', true) +
    `</div>` +
    (q.history && q.history.length > 1 ? `<details class="o-fold" data-okeep="qh-${esc(q.id)}"${keepOpen('qh-' + q.id)}><summary>This question’s record (${q.history.length})</summary>${q.history.map(recordLine).join('')}</details>` : '') +
    `</section>`;
}

function closedQuestionHtml(q) {
  const a = q.answer || {};
  const res = q.resolution || {};
  const outcome = q.state === 'answered'
    ? `Answered by ${a.actor || 'a person'} ${when(a.at)}: ${esc(a.choice || a.note || '')}${a.choice && a.note ? ' — ' + esc(a.note) : ''}`
    : `${esc(q.state)}${res.by || res.actor ? ' by ' + esc(res.by || res.actor) : ''} ${when(res.at)}${res.note ? ': ' + esc(res.note) : ''}`;
  return `<section class="o-q closed"><p class="o-eyebrow">question ${esc(q.id)} · ${esc(q.state)}</p>` +
    `<h4>${esc(q.text || '')}</h4><p>${outcome}</p>` +
    (q.options && q.options.length ? `<p class="o-dim">Options were: ${q.options.map(esc).join(' · ')}</p>` : '') + `</section>`;
}

function aggregatedHtml(d) {
  const agg = d.aggregated || [];
  if (!agg.length) return '';
  return `<section class="o-agg"><h4>Its members’ questions</h4><p class="o-dim">Each is answered on the member card that owns it.</p><ul class="o-rows">` +
    agg.map(q => {
      const m = q.origin || {};
      const mt = taskByFile(m.file);
      return `<li class="o-row"><span>${badge(q.state, q.state === 'open' ? 'alarm' : 'idle')} ${esc(q.text || '')}</span>` +
        `<a data-route href="${esc(orchHref('decisions', mt ? idOf(mt) : m.card))}">answer on ${esc('#' + (m.card || m.file))}</a></li>`;
    }).join('') + `</ul></section>`;
}

function responsesHtml(t, d) {
  const list = d.responses || [];
  if (!list.length) return '';
  return `<details class="o-fold" data-okeep="resp-${esc(t.file)}"${keepOpen('resp-' + t.file, true)}><summary>Reasoning results (${list.length}) — agent-reported</summary>` +
    list.map(r => `<section class="o-resp"><p class="o-eyebrow">${esc(r.operation || '')} · ${esc(r.need || '')} · ${badge(r.status || '?', r.status === 'valid' ? 'neutral' : 'alarm')} · ${when(r.at)}` +
      `${r.model ? ' · model ' + esc(r.model) : ''}${r.harness ? ' · ' + esc(r.harness) : ''}${r.agent ? ' · ' + esc(r.agent) : ''}</p>` +
      `<p class="o-src">${esc(SOURCE_WORDS['agent-reported'])}</p>` +
      (r.summary ? `<div class="o-md">${md(r.summary, { base: 5 })}</div>` : '') +
      ((r.findings || []).length ? `<h5>Findings</h5><ul>${r.findings.map(f => `<li>${badge(f.severity || 'note', 'neutral')} ${esc(f.statement || '')} ${refsHtml(f.evidence)}</li>`).join('')}</ul>` : '') +
      ((r.actions || []).length ? `<h5>Proposed actions — proposals, not executed</h5><ul>${r.actions.map(a => `<li><code>${esc(a.action || '')}</code> ${esc(a.card || '')} — ${esc(a.why || '')}</li>`).join('')}</ul>` : '') +
      ((r.blockers || []).length ? `<h5>Blockers</h5><ul>${r.blockers.map(b => `<li>${esc(b.blocker || '')}</li>`).join('')}</ul>` : '') +
      ((r.diagnostics || []).length ? `<p class="o-bad">Refused or ended: ${esc(r.diagnostics.join(', '))}</p>` : '') +
      `</section>`).join('') + `</details>`;
}

function recordLine(r) {
  const text = r.text || r.summary || r.choice || r.note || r.outcome || '';
  return `<p class="o-rec-line"><span class="o-src">${esc(r.at || '')}</span> ${esc(r.kind)} · ${esc(SOURCE_WORDS[r.source] || r.source || '')}` +
    `${r.actor ? ' · ' + esc(r.actor) : ''} — ${esc(String(text).slice(0, 400))}</p>`;
}

function recordHtml(t, d) {
  const blockers = d.blockers || [];
  const attempts = d.attempts || [];
  return (blockers.length ? `<h4>Blockers</h4><ul class="o-rows">${blockers.map(b =>
    `<li class="o-row">${badge(b.state, b.state === 'open' ? 'alarm' : 'idle')} ${esc(b.text || b.blocker || '')} <span class="o-src">${esc(SOURCE_WORDS[b.source] || '')}</span></li>`).join('')}</ul>` : '') +
    (attempts.length ? `<h4>Recovery attempts</h4><ul class="o-rows">${attempts.map(a =>
      `<li class="o-row">attempt ${esc(a.attempt)} of ${esc(a.of)} · ${esc(a.outcome || '')} — ${esc(a.summary || '')}</li>`).join('')}</ul>` : '') +
    ((d.timeline || []).length ? `<details class="o-fold" data-okeep="tl-${esc(t.file)}"${keepOpen('tl-' + t.file)}><summary>Full orchestration record (${d.timeline.length})</summary>${d.timeline.map(recordLine).join('')}</details>` : '');
}

/* ── phases ──────────────────────────────────────────────────────────── */

function phasesHtml() {
  const sch = orch().schedule || {};
  const list = ((sch.phases || {}).phases) || [];
  const sel = selected();
  if (!list.length) return `<p class="o-empty">No phase card is on this board.</p>`;
  const bench = scopeOf('bench');
  return (bench && bench.paused ? `<p class="o-bad" role="status">The Bench scope is paused, and its pause reaches every phase scope below.</p>` : '') +
    (S.orchItem && !sel ? `<p class="o-empty" role="alert">No card #${esc(S.orchItem)} on this board.</p>` : '') +
    list.map(p => phaseHtml(p, sel && sel.file === p.card)).join('');
}

function phaseHtml(p, isSel) {
  const t = taskByFile(p.card);
  const scope = p.scope ? scopeOf(p.scope) : null;
  const n = p.number;
  const acts = [];
  const r = orch().reading;
  if (p.named && p.mode !== 'scoped') {
    acts.push(action(`adopt-phase:${n}`, 'Adopt into its phase scope', 'phase', { card: p.card, action: 'adopt' },
      EFFECTS.adoptPhase + (p.adoptable ? '' : ' Not adoptable now: ' + [].concat(p.working || []).join(', '))));
  }
  if (p.mode === 'scoped') {
    acts.push(action(`release-phase:${n}`, 'Release to the phase runner', 'phase', { card: p.card, action: 'release' }, EFFECTS.releasePhase));
  }
  if (scope && r.effective.valid) {
    acts.push(scope.paused
      ? action(`resume:${scope.id}`, 'Resume this phase', 'scope', { scope: scope.id, action: 'resume' }, EFFECTS.resumePhase)
      : action(`pause:${scope.id}`, 'Pause this phase', 'scope', { scope: scope.id, action: 'pause' }, EFFECTS.pausePhase));
  }
  const [word, tone] = scope ? scopeStateWords(scope) : [p.owner === 'phase runner' ? 'Phase runner' : 'Not named', 'idle'];
  const members = (p.members || []).map(m => {
    const mt = taskByFile(m.card);
    return `<li class="o-row">${mt ? cardLink(mt, num(mt) + ' ' + title(mt)) : esc(m.card)}` +
      `<span>${badge(m.state, m.state === 'working' ? 'working' : m.state === 'integrated' ? 'calm' : 'neutral')} ${esc(m.why || '')}</span>` +
      `<span class="o-dim">${esc(m.stage)}</span></li>`;
  });
  return `<article class="o-panel o-phase${isSel ? ' sel' : ''}" id="ophase-${esc(n)}" aria-label="Phase ${esc(n)}">` +
    `<h3>${t ? cardLink(t, num(t)) : esc('#' + n)} ${esc(title(t))} ${badge(word, tone)}</h3>` +
    `<dl class="o-kv"><div><dt>scheduling owner</dt><dd>${esc(p.owner)}${p.mode ? ` · mode ${esc(p.mode)}` : ''}</dd></div>` +
    `<div><dt>agreement</dt><dd>${p.named ? `named (${esc(p.scope)}) · strategy ${esc(p.strategy || 'sequential')}` : 'not named — the phase runner schedules it'}</dd></div>` +
    `<div><dt>integrated</dt><dd>${(p.integrated || []).length ? esc((p.integrated || []).map(x => '#' + x).join(', ')) + ' — in the phase branch, not delivered' : 'none yet'}</dd></div>` +
    (p.delivered !== undefined ? `<div><dt>delivered</dt><dd>${p.delivered ? 'yes' : 'not yet'}</dd></div>` : '') +
    `</dl>` +
    (scope ? `<details class="o-fold" data-okeep="pauth-${esc(n)}"${keepOpen('pauth-' + n, isSel)}><summary>Effective authority and capacity (parent ∩ phase)</summary>${authorityHtml(scope)}${capacityHtml(scope)}</details>` : '') +
    (members.length ? `<h4>Members</h4><ul class="o-rows">${members.join('')}</ul>` : '') +
    (acts.length ? `<h4>Actions</h4><div class="o-actions">${acts.join('')}</div>` : '') +
    `<p><a data-route href="/phases/${esc(n)}">Open its lane in Phases</a></p></article>`;
}

/* ── recovery ────────────────────────────────────────────────────────── */

function recoveryHtml() {
  const sch = orch().schedule || {};
  const rec = sch.recovery || {};
  const r = orch().reading;
  const ops = r.operations || {};
  const br = rec.breaker || sch.breaker || {};
  const held = rec.held || [];
  const others = (rec.incidents || []).filter(i => !held.includes(i) && !held.some(h => h.id === i.id));
  const parts = [];
  parts.push(`<article class="o-panel"><h3>Circuit breaker ${badge(br.open ? 'Open' : 'Closed', br.open ? 'alarm' : 'calm')}</h3>` +
    `<p>${esc(`${br.failures || 0} failures in the window (opens at ${br.threshold || '?'})`)}${br.open && br.until ? ` · closes ${esc(new Date(br.until * 1000).toLocaleTimeString())}` : ''}</p>` +
    ((br.cards || []).length ? `<p>Behind it: ${esc(br.cards.join(', '))}</p>` : '') +
    (br.open ? action('reset-breaker', 'Reset the breaker', 'recovery', { action: 'reset-breaker' }, EFFECTS.resetBreaker) : '') +
    `</article>`);
  const unknown = ops.held || [];
  const refused = (ops.recent || []).filter(op => op.status === 'refused');
  if (unknown.length || refused.length) {
    parts.push(`<article class="o-panel"><h3>Operations held for you</h3>` +
      unknown.map(op => opHtml(op, true)).join('') + refused.map(op => opHtml(op, false)).join('') + `</article>`);
  }
  parts.push(`<article class="o-panel"><h3>Incidents needing you <span class="o-count">${held.length}</span></h3>` +
    (held.length ? held.map(incidentHtml).join('') : '<p class="o-dim">No held or exhausted incident.</p>') + `</article>`);
  if (others.length) {
    parts.push(`<details class="o-panel o-fold" data-okeep="incidents"${keepOpen('incidents')}><summary>Other incidents (${others.length})</summary>${others.map(incidentHtml).join('')}</details>`);
  }
  const runs = rec.runs || [];
  parts.push(`<article class="o-panel"><h3>Observed runs <span class="o-count">${runs.length}</span></h3>` +
    (runs.length ? runs.map(run => {
      const t = taskByFile(run.card);
      return `<section class="o-run"><h4>${t ? cardLink(t, num(t) + ' ' + title(t)) : esc(run.card)} · ${esc(run.name || run.agent || '')}` +
        `${run.orchestrated ? '' : ' <span class="o-dim">(a person’s run — observed, never diagnosed or stopped)</span>'}</h4>${signalsHtml(run)}</section>`;
    }).join('') : '<p class="o-dim">No managed run is being observed.</p>') +
    ((rec.notes || []).map(n => `<p class="o-dim">${esc(n)}</p>`).join('')) + `</article>`);
  return parts.join('');
}

function incidentHtml(i) {
  const t = taskByFile(i.file);
  const exhausted = i.status === 'exhausted';
  return `<section class="o-incident" aria-label="Incident ${esc(i.id)}">` +
    `<h4>${t ? cardLink(t, num(t) + ' ' + title(t)) : esc(i.file || i.card || '')} ${badge(exhausted ? 'Exhausted' : i.status, ['held', 'exhausted'].includes(i.status) ? 'alarm' : 'neutral')}</h4>` +
    `<p>${esc(i.type || '')} · ${esc(i.code || '')} — ${esc(i.reason || '')}</p>` +
    `<p>${esc(`${i.launched || 0} of ${i.cap ?? '?'} diagnostic attempts launched`)}` +
    `${exhausted ? ' — no further automatic attempt will launch' : ''}</p>` +
    (i.failure ? `<p class="o-dim">${esc(`failure classified ${i.failure.class || '?'}: ${i.failure.headline || ''}`)}</p>` : '') +
    ((i.attempts || []).length ? `<ul>${i.attempts.map(a => `<li>attempt ${esc(a.n)} · ${esc(a.status || '')} ${esc(a.outcome || '')}${a.proposed ? ' · proposed ' + esc(a.proposed) : ''}</li>`).join('')}</ul>` : '') +
    ((i.choices || []).length ? `<h5>Your choices</h5><ul>${i.choices.map(c => `<li>${esc(c)}</li>`).join('')}</ul>` : '') +
    (i.status !== 'resolved' ? action(`resolve:${i.id}`, 'Mark handled', 'recovery', { action: 'resolve', incident: i.id }, EFFECTS.resolveIncident) : '') +
    (t ? `<p><a data-route href="${esc(orchHref('decisions', idOf(t)))}">Its diagnoses and attempts on the card</a></p>` : '') +
    `</section>`;
}

function opHtml(op, unknown) {
  const t = taskByFile(op.file);
  const head = `<p class="o-eyebrow">${esc(op.key)} · ${esc(op.action || '')} · ${esc(op.status)}</p>` +
    `<p>${t ? cardLink(t, num(t) + ' ' + title(t)) : esc(op.file || '')} — ${esc(op.reason || op.observed || op.note || '')}</p>`;
  if (unknown) {
    return `<section class="o-op">${head}<p class="o-bad">The outcome is unknown: the intent was recorded and no receipt followed. Bench will not repeat it blindly.</p>` +
      `<div class="o-actions">` +
      action(`op-launched:${op.key}`, 'It was launched', 'operation', { operation: op.key, decision: 'launched' }, EFFECTS.launched) +
      action(`op-not:${op.key}`, 'Nothing was launched', 'operation', { operation: op.key, decision: 'not-launched' }, EFFECTS.notLaunched) +
      `</div></section>`;
  }
  return `<section class="o-op">${head}` +
    action(`op-release:${op.key}`, 'Release to try again', 'operation', { operation: op.key, decision: 'release' }, EFFECTS.release) + `</section>`;
}

/* ── activity ────────────────────────────────────────────────────────── */

function activityHtml() {
  const r = orch().reading;
  const ops = (r.operations || {}).recent || [];
  const events = ((S.state && S.state.boardEvents) || [])
    .filter(e => /orchestration|question|answered|withdrawn/.test(String(e.summary || '')))
    .slice(-30).reverse();
  return `<article class="o-panel"><h3>Operations, newest first</h3>` +
    `<p class="o-dim">Each side effect has an intent written before it and a receipt after; the same records are on the card they are about.</p>` +
    (ops.length ? `<ul class="o-rows">${ops.map(op => {
      const t = taskByFile(op.file);
      return `<li class="o-row"><span class="o-src">${esc(op.updatedAt || op.intentAt || '')}</span>` +
        `<span>${esc(op.action || '')} ${t ? cardLink(t) : esc(op.file || '')} · ${badge(op.status, op.status === 'unknown' ? 'alarm' : 'neutral')}` +
        `${op.outcome ? ' ' + esc(op.outcome) : ''}</span><span class="o-dim">${esc(op.reason || op.observed || '')}</span></li>`;
    }).join('')}</ul>` : '<p class="o-dim">No operation on record.</p>') + `</article>` +
    `<article class="o-panel"><h3>People’s changes on this board</h3>` +
    (events.length ? `<ul class="o-rows">${events.map(e => `<li class="o-row"><span class="o-src">${esc(e.ts ? ago(e.ts) + ' ago' : '')}</span>` +
      `<span>${esc(e.summary || '')}</span></li>`).join('')}</ul>` : '<p class="o-dim">Nothing this session.</p>') + `</article>`;
}

/* ── the operating agreement ─────────────────────────────────────────── */

const AUTHORITY_WORDS = {
  readiness: 'Check readiness', reasoning: 'Launch reasoning agents',
  implement: 'Start committed work', review: 'Request checks and independent review',
  repair: 'Repair within the ceiling', recovery: 'Diagnose and unstick',
  integrate: 'Integrate phase members', merge: 'Merge accepted work',
};
const POOLS = ['implementation', 'review', 'reasoning', 'dailyStarts'];
const CEILINGS = ['launchesPerCard', 'repairRounds', 'transientRetries', 'runAgeMinutes', 'recoveryAttempts'];

function editState() {
  const o = orch();
  const r = o.reading;
  const draft = r && r.draft;
  if (!draft) { o.edit = null; return null; }
  if (!o.edit || (o.edit.base !== draft.revision && !o.edit.dirty)) {
    o.edit = { base: draft.revision, doc: JSON.parse(JSON.stringify(draft.document || {})), dirty: false };
  }
  return o.edit;
}

function field(path, type, value, label, extra = '') {
  const id = 'ag-' + path.replace(/[^a-z0-9]/gi, '-');
  if (type === 'bool' || type === 'pauth') {
    return `<label class="o-setting" for="${id}"><span>${esc(label)}</span>` +
      `<input id="${id}" type="checkbox" data-ofield="ag" data-type="${type}" data-path="${esc(path)}" data-o="${id}"${value ? ' checked' : ''}${extra}></label>`;
  }
  if (type === 'strategy') {
    return `<label class="o-setting" for="${id}"><span>${esc(label)}</span><select id="${id}" data-ofield="ag" data-type="str" data-path="${esc(path)}" data-o="${id}">` +
      ['sequential', 'dependencies'].map(s => `<option${value === s ? ' selected' : ''}>${s}</option>`).join('') + `</select></label>`;
  }
  if (type === 'list') {
    return `<label class="o-setting" for="${id}"><span>${esc(label)}</span>` +
      `<input id="${id}" type="text" inputmode="numeric" data-ofield="ag" data-type="list" data-path="${esc(path)}" data-o="${id}" value="${esc((value || []).join(', '))}"></label>`;
  }
  return `<label class="o-setting" for="${id}"><span>${esc(label)}</span>` +
    `<input id="${id}" type="number" min="0" max="${path.endsWith('runAgeMinutes') ? 1440 : 1000}" data-ofield="ag" data-type="${type}" data-path="${esc(path)}" data-o="${id}"` +
    ` value="${value === undefined || value === null ? '' : esc(value)}"${type === 'intopt' ? ' placeholder="inherit"' : ''}></label>`;
}

function benchFormHtml(doc) {
  const b = doc.bench || {};
  const auth = b.authority || {};
  const cap = b.capacity || {};
  return `<fieldset class="o-form"><legend>Bench scope</legend>` +
    field('bench.enabled', 'bool', b.enabled, 'Enabled — launch-capable once applied and this installation executes') +
    field('bench.paused', 'bool', b.paused, 'Paused') +
    `<h4>Authority</h4>` +
    Object.keys(AUTHORITY_WORDS).map(w => field(`bench.authority.${w}`, 'bool', auth[w], `${AUTHORITY_WORDS[w]} (${w})`)).join('') +
    `<p class="o-dim">Release and deployment are never delegated.</p>` +
    `<h4>Capacity — one shared total</h4>` + POOLS.map(p => field(`bench.capacity.${p}`, 'int', cap[p], p)).join('') +
    `<h4>Ceilings</h4>` + CEILINGS.map(c => field(`bench.capacity.${c}`, 'int', cap[c], c)).join('') +
    field('bench.exclusions', 'list', b.exclusions, 'Excluded cards (numbers)') +
    field('bench.handback', 'list', b.handback, 'Handed back to you (numbers)') +
    `</fieldset>`;
}

function phaseFormHtml(doc, n) {
  const p = (doc.phases || {})[n] || {};
  const parent = ((doc.bench || {}).authority) || {};
  const auth = p.authority || {};
  const alloc = p.allocation || {};
  const ceil = p.ceilings || {};
  return `<fieldset class="o-form"><legend>Phase #${esc(n)} — may only narrow</legend>` +
    field(`phases.${n}.enabled`, 'bool', p.enabled, 'Enabled') +
    field(`phases.${n}.paused`, 'bool', p.paused, 'Paused') +
    field(`phases.${n}.strategy`, 'strategy', p.strategy || 'sequential', 'Strategy') +
    `<h4>Authority (inherits the parent's; unchecking narrows)</h4>` +
    Object.keys(AUTHORITY_WORDS).map(w => field(`phases.${n}.authority.${w}`, 'pauth', parent[w] && auth[w] !== false,
      `${AUTHORITY_WORDS[w]}${parent[w] ? '' : ' — the parent does not grant it'}`, parent[w] ? '' : ' disabled')).join('') +
    `<h4>Allocation from the Bench total</h4>` + POOLS.map(k => field(`phases.${n}.allocation.${k}`, 'int', alloc[k] || 0, k)).join('') +
    `<h4>Ceilings (blank inherits; may only be lower)</h4>` + CEILINGS.map(k => field(`phases.${n}.ceilings.${k}`, 'intopt', ceil[k], k)).join('') +
    `<div class="o-actions">${btn(`rmphase:${n}`, `Remove phase #${n} from the draft`, { act: 'rmphase', data: { phase: n } })}</div>` +
    `</fieldset>`;
}

function changeWords(c) {
  const v = (x) => x && typeof x === 'object' ? (x.total !== undefined ? `${x.total} total, ${x.own} own` : JSON.stringify(x)) : JSON.stringify(x);
  return `${c.scope ? scopeName(c.scope) + ': ' : ''}${c.what} ${v(c.before)} → ${v(c.after)}`;
}

function problemsHtml(list) {
  if (!list || !list.length) return '';
  return `<ul class="o-problems" role="alert">${list.map(p =>
    `<li><code>${esc(p.code || '')}</code> ${esc(p.path || '')} — ${esc(p.message || '')}</li>`).join('')}</ul>`;
}

function agreementHtml() {
  const o = orch();
  const r = o.reading;
  const eff = r.effective;
  const draft = r.draft;
  const edit = editState();
  const parts = [];
  parts.push(`<article class="o-panel"><h3>In force ${badge(eff.configured ? (eff.valid ? `revision ${eff.revision}` : 'invalid') : 'none', eff.configured && eff.valid ? 'calm' : eff.configured ? 'alarm' : 'idle')}</h3>` +
    (eff.configured ? `<p>${esc(eff.scopes.map(s => `${scopeName(s.id)}: ${scopeStateWords(s)[0]}`).join(' · '))}</p>` : '<p>No agreement: orchestration is off and no card or run is touched.</p>') +
    problemsHtml(eff.problems) +
    `<p class="o-dim">Release and deployment are never delegated. Notifications: board only — there is no connector, chat or email.</p></article>`);
  if (!draft) {
    parts.push(`<article class="o-panel"><h3>No draft</h3><p>Start a draft to change the agreement. A draft is only a draft: saving it changes nothing that runs.</p><div class="o-actions">` +
      (eff.document ? btn('start-draft-from', 'Start a draft from the agreement in force', { act: 'startDraft', data: { from: 'effective' }, primary: true }) : '') +
      btn('start-draft-new', 'Start a new draft (defaults, Bench scope off)', { act: 'startDraft', data: { from: 'new' }, primary: !eff.document }) +
      `</div></article>`);
  } else {
    const doc = edit.doc;
    const phaseKeys = Object.keys(doc.phases || {}).sort((a, b) => (+a) - (+b));
    const preview = o.preview && o.preview.base === edit.base ? o.preview : null;
    parts.push(`<article class="o-panel o-draft"><h3>Draft ${badge(draft.valid ? `revision ${draft.revision}` : 'has problems', draft.valid ? 'neutral' : 'alarm')}` +
      `${edit.dirty ? ' ' + badge('unsaved edits', 'alarm') : ''}</h3>` +
      problemsHtml(draft.problems) +
      (draft.changes && draft.changes.length ? `<details class="o-fold" data-okeep="saved-changes"${keepOpen('saved-changes', true)}><summary>What applying the saved draft would change (${draft.changes.length})</summary><ul>${draft.changes.map(c => `<li>${esc(changeWords(c))}</li>`).join('')}</ul></details>`
        : `<p class="o-dim">The saved draft changes nothing against what is in force.</p>`) +
      benchFormHtml(doc) + phaseKeys.map(n => phaseFormHtml(doc, n)).join('') +
      `<fieldset class="o-form"><legend>Add a phase scope</legend><label class="o-setting" for="ag-newphase"><span>Phase card number</span>` +
      `<input id="ag-newphase" type="text" inputmode="numeric" data-ofield="newphase" data-o="ag-newphase" value="${esc(o.newPhase || '')}"></label>` +
      `<div class="o-actions">${btn('addphase', 'Add to the draft', { act: 'addphase' })}</div>` +
      `<p class="o-dim">Naming a phase does not take it from the phase runner: a person adopts it in Phases once it is quiet.</p></fieldset>` +
      `<div class="o-steps">` +
      action('save-draft', 'Save draft', 'saveDraft', {}, EFFECTS.saveDraft) +
      `<div class="o-action">${btn('preview', 'Preview against what is in force', { act: 'preview' })}<p class="o-effect">Compares two resolutions; writes nothing.</p></div>` +
      `<div class="o-action">${btn('apply', 'Apply the saved draft', { act: 'apply', primary: true, disabled: edit.dirty || !draft.valid, why: edit.dirty ? 'save your edits first, then apply' : !draft.valid ? 'the draft has problems' : '' })}` +
      `<p class="o-effect">${esc(EFFECTS.apply)}${edit.dirty ? ' Save your edits first — Apply puts the saved draft in force, not the page.' : ''}</p></div>` +
      action('discard', 'Discard the draft', 'discard', {}, EFFECTS.discard, true) +
      (edit.dirty ? `<div class="o-action">${btn('revert', 'Revert unsaved edits', { act: 'revert' })}</div>` : '') +
      `</div>` +
      (preview ? `<section class="o-preview" role="status"><h4>Preview — nothing was written</h4>${problemsHtml(preview.problems)}` +
        (preview.changes.length ? `<ul>${preview.changes.map(c => `<li>${esc(changeWords(c))}</li>`).join('')}</ul>` : '<p>No change against what is in force.</p>') +
        (preview.ungated ? `<p><strong>Merge delegated with ungated rules:</strong> ${esc(preview.ungated)} — no checks are required by your policy, so such a merge is not verified.</p>` : '') +
        `<p class="o-dim">Existing To Do, In Progress and Review cards are neither started nor changed by applying; after a handover, working cards need adopting.</p></section>` : '') +
      `</article>`);
  }
  parts.push(mergeHtml());
  parts.push(installationHtml());
  return parts.join('');
}

function ungatedWords(doc) {
  const r = orch().reading;
  const merge = ((doc.bench || {}).authority || {}).merge;
  if (!merge) return '';
  return Object.entries((r.mergeRules || {}).scopes || {}).filter(([, v]) => !v.gated).map(([k]) => k).join(', ');
}

function setPath(doc, path, type, raw, checked) {
  const keys = path.split('.');
  let at = doc;
  for (const k of keys.slice(0, -1)) {
    if (!at[k] || typeof at[k] !== 'object') at[k] = {};
    at = at[k];
  }
  const last = keys[keys.length - 1];
  if (type === 'bool') at[last] = !!checked;
  else if (type === 'pauth') { if (checked) delete at[last]; else at[last] = false; }
  else if (type === 'int') { const n = parseInt(raw, 10); at[last] = Number.isNaN(n) ? 0 : n; }
  else if (type === 'intopt') { const n = parseInt(raw, 10); if (raw === '' || Number.isNaN(n)) delete at[last]; else at[last] = n; }
  else if (type === 'list') at[last] = String(raw).split(/[\s,]+/).map(s => s.replace(/^#/, '')).filter(Boolean);
  else at[last] = String(raw);
}

/* ── doing ───────────────────────────────────────────────────────────── */

async function doAction(el) {
  const o = orch();
  const r = o.reading;
  const d = el.dataset;
  const key = d.o;
  const expected = r && r.effective.revision;
  switch (d.oact) {
    case 'reload': loadOrch(true); return;
    case 'dismiss': o.notice = null; paintNow(); return;
    case 'scope':
      return armed(key, async () => {
        const res = await send('/api/orchestration/scope', { scope: d.scope, action: d.action, expected,
          ...(d.card ? { card: d.card } : {}) }, true);
        return res.ok ? { said: `${d.action} ${scopeName(d.scope)}${d.card ? ' · #' + d.card : ''} — recorded; nothing was started or stopped` } : res;
      });
    case 'install':
      return armed(key, async () => {
        const res = await send('/api/orchestration/installation', { action: d.action,
          expected: (r.installation || {}).revision, ...(d.card ? { card: d.card } : {}) }, true);
        return res.ok ? { said: `${d.action} — recorded` } : res;
      });
    case 'phase':
      return armed(key, async () => {
        const res = await send('/api/orchestration/phase', { card: d.card, action: d.action }, true);
        return res.ok ? { said: `phase ${d.action}ed — nothing was launched` } : res;
      });
    case 'operation':
      return armed(key, async () => {
        const res = await send('/api/orchestration/operation', { operation: d.operation, decision: d.decision }, true);
        return res.ok ? { said: `operation settled: ${d.decision} — nothing was launched` } : res;
      });
    case 'recovery':
      return armed(key, async () => {
        const res = await send('/api/orchestration/recovery', { action: d.action, ...(d.incident ? { incident: d.incident } : {}) }, true);
        return res.ok ? { said: d.action === 'resolve' ? 'incident marked handled' : 'circuit breaker reset' } : res;
      });
    case 'cancel':
      return armed(key, async () => {
        const res = await send('/api/agent/stop', { id: d.agent }, false);
        return res.ok ? { said: 'stop requested — the run reads stopped once its process is observed gone; its branch and worktree are kept' } : res;
      });
    case 'answer':
    case 'withdraw':
      return armed(key, () => answer(d.file, d.q, d.oact));
    case 'startDraft':
      return armed(key, async () => {
        const doc = d.from === 'effective' ? r.effective.document : 'new';
        const res = await send('/api/orchestration/agreement/draft', { document: doc, expected: 'none' }, true);
        return res.ok ? { said: 'draft started — nothing in force changed' } : res;
      });
    case 'saveDraft':
      return armed(key, async () => {
        const edit = o.edit;
        const res = await send('/api/orchestration/agreement/draft', { document: edit.doc, expected: edit.base }, true);
        if (res.ok) { o.edit = null; return { said: 'draft saved — nothing in force changed' + (res.data.result && !res.data.result.ok ? '; it has problems, shown below' : '') }; }
        return res;
      });
    case 'preview': {
      const edit = o.edit;
      if (!edit) return;
      const res = await send('/api/orchestration/agreement/preview', { document: edit.doc }, true);
      if (!res.ok) { o.notice = { tone: 'alarm', text: res.error }; paintNow(); return; }
      o.preview = { base: edit.base, ...res.data.result, ungated: ungatedWords(edit.doc) };
      paintNow();
      return;
    }
    case 'apply':
      return armed(key, async () => {
        const res = await send('/api/orchestration/agreement/apply', { expected, draft: r.draft.revision }, true);
        if (res.ok) { o.edit = null; o.preview = null; return { said: `agreement ${res.data.result.revision} in force — nothing was started by applying it` }; }
        return res;
      });
    case 'discard':
      return armed(key, async () => {
        const res = await send('/api/orchestration/agreement/discard', { expected: r.draft.revision }, true);
        if (res.ok) { o.edit = null; o.preview = null; return { said: 'draft discarded' }; }
        return res;
      });
    case 'revert': o.edit = null; o.preview = null; paintNow(); return;
    case 'addphase': {
      const n = String(parseInt(o.newPhase, 10));
      if (!o.edit || n === 'NaN' || +n <= 0) { o.notice = { tone: 'alarm', text: 'a phase is named by its card number' }; paintNow(); return; }
      o.edit.doc.phases = o.edit.doc.phases || {};
      if (!o.edit.doc.phases[n]) o.edit.doc.phases[n] = { enabled: true, paused: false, strategy: 'sequential', allocation: {}, ceilings: {} };
      o.edit.dirty = true;
      o.newPhase = '';
      paintNow();
      return;
    }
    case 'rmphase':
      if (o.edit && o.edit.doc.phases) { delete o.edit.doc.phases[d.phase]; o.edit.dirty = true; }
      paintNow();
      return;
    default:
  }
}

async function answer(file, qid, kind) {
  const o = orch();
  const t = taskByFile(file);
  const data = (o.dec[file] || {}).data;
  if (!t || !data) return { ok: false, error: 'the card’s history is not read yet — nothing was sent' };
  const q = data.questions.find(x => x.id === qid);
  const form = o.forms[qid] || {};
  if (!q) return { ok: false, error: 'that question is no longer on the card — nothing was sent' };
  const rkey = `${kind}:${qid}:${q.basis}`;
  const request = o.reqs[rkey] || (o.reqs[rkey] = newRequest(kind === 'answer' ? 'ans' : 'wd'));
  const body = { file, stage: t.stage, question: qid, basis: q.basis, note: form.note || '', request };
  if (kind === 'answer') {
    Object.assign(body, { briefSha: data.briefSha, actor: (data.actor || {}).name || '' });
    if (form.choice !== null && form.choice !== undefined) body.choice = form.choice;
  }
  const res = await send(`/api/card/decisions/${kind}`, body, false);
  loadDecisions(t, true);
  if (!res.ok) {
    const why = res.data && res.data.stale
      ? `Not recorded: ${res.error}. The page has read the question again.`
      : res.data && res.data.refused ? `Refused: ${res.error}` : res.error;
    return { ok: false, error: why };
  }
  delete o.forms[qid];
  return { said: res.data.duplicate ? 'already recorded — the first answer stands' : kind === 'answer' ? 'answer recorded on the card' : 'question withdrawn' };
}

/* ── painting ────────────────────────────────────────────────────────── */

function keepOpen(key, dflt = false) {
  const o = orch();
  const v = o.open[key];
  return (v === undefined ? dflt : v) ? ' open' : '';
}

function editing(root) {
  const a = document.activeElement;
  if (!a || !root.contains(a)) return false;
  if (a.tagName === 'TEXTAREA' || a.tagName === 'SELECT') return true;
  return a.tagName === 'INPUT' && !['checkbox', 'radio', 'button'].includes(a.type);
}

function paint(root, html) {
  const a = document.activeElement;
  const mark = a && root.contains(a) ? a.getAttribute('data-o') : null;
  if (!setHtml(root, html)) return;
  if (mark) {
    const el = root.querySelector(`[data-o="${CSS.escape(mark)}"]`);
    if (el && typeof el.focus === 'function') el.focus({ preventScroll: true });
  }
}

function paintNow() {
  const root = $('#oshell');
  if (root && S.view === 'orchestrator') paint(root, shellHtml());
}

function wire(root) {
  const o = orch();
  if (o.wired === root) return;
  o.wired = root;
  root.addEventListener('click', (e) => {
    const el = e.target.closest && e.target.closest('[data-oact]');
    if (!el || el.disabled) return;
    e.preventDefault();
    doAction(el);
  });
  const onField = (e) => {
    const el = e.target;
    const f = el && el.dataset && el.dataset.ofield;
    if (!f) return;
    if (f === 'choice') {
      const form = o.forms[el.dataset.q] || (o.forms[el.dataset.q] = { choice: null, note: '' });
      form.choice = parseInt(el.value, 10);
      if (e.type === 'change') paintNow();
    } else if (f === 'note') {
      const form = o.forms[el.dataset.q] || (o.forms[el.dataset.q] = { choice: null, note: '' });
      form.note = el.value;
    } else if (f === 'newphase') {
      o.newPhase = el.value;
    } else if (f === 'ag' && o.edit) {
      setPath(o.edit.doc, el.dataset.path, el.dataset.type, el.value, el.checked);
      o.edit.dirty = true;
      if (e.type === 'change' && (el.type === 'checkbox')) paintNow();
    }
  };
  root.addEventListener('input', onField);
  root.addEventListener('change', onField);
  // `toggle` does not bubble: caught on the way down, so an open fold is
  // remembered across every repaint
  root.addEventListener('toggle', (e) => {
    const k = e.target && e.target.dataset && e.target.dataset.okeep;
    if (k) o.open[k] = e.target.open;
  }, true);
  // a field left: the repaint it held may run now
  root.addEventListener('focusout', () => setTimeout(scheduleRender, 0));
}

/* The view's paint: reads what is stale, holds while a person types, and
   never writes. */
function renderOrchestrator() {
  const root = $('#oshell');
  if (!root || !S.state) return;
  wire(root);
  loadOrch(false);
  if (editing(root)) return;
  paint(root, shellHtml());
}

/* The board's strip: only while an agreement is in force, and only ever a
   way in. Read from `/api/state` alone, so drawing it asks for nothing. */
function renderOrchStrip() {
  const el = $('#orchstrip');
  if (!el) return;
  const sum = S.state && S.state.orchestration;
  if (!sum || !sum.configured) { el.hidden = true; setHtml(el, ''); return; }
  const f = facts();
  const st = overall({ ...f, unknownOps: 0, heldIncidents: 0, breakerOpen: false, adoptionRequired: false,
                       replica: false, reasoning: false, launching: false });
  const open = f.openDecisions;
  const paused = (sum.scopes || []).filter(s => s.kind === 'phase' && s.pausedBy && s.pausedBy !== 'bench').length;
  const html = `<span class="o-eyebrow">Orchestrator</span> ${badge(st.label, st.tone)}` +
    `<span class="o-striptext">${esc(f.managed)} managed card${f.managed === 1 ? '' : 's'}` +
    `${open ? ` · ${open} open decision${open === 1 ? '' : 's'}` : ''}${paused ? ` · ${paused} phase${paused === 1 ? '' : 's'} paused` : ''}` +
    ` · agreement ${esc(sum.revision)}</span>` +
    (open ? `<a class="o-btn" data-route href="${esc(orchHref('decisions'))}">Answer decisions</a>` : '') +
    `<a class="o-btn" data-route href="/orchestrator">Open Orchestrator</a>`;
  el.hidden = false;
  setHtml(el, html);
}

/* The switcher's label: how many decisions wait, and a mark when the
   agreement is paused or invalid — learned from whichever view you are on. */
function renderOrchTab() {
  const a = document.querySelector('#views [data-view="orchestrator"]');
  if (!a) return;
  const sum = (S.state && S.state.orchestration) || {};
  const open = openDecisionCount();
  const alarm = sum.status === 'paused' || sum.status === 'invalid';
  const html = 'Orchestrator' + (open ? `<span class="vcount">${open}</span>` : '') +
    (alarm ? '<span class="vhalt"></span>' : '');
  if (a.innerHTML !== html) a.innerHTML = html;
  a.title = alarm ? `orchestration is ${sum.status} — open the Orchestrator`
    : open ? `${open} decision${open === 1 ? '' : 's'} wait on you`
      : sum.configured ? 'Managed work, phase scopes, decisions, recovery and the operating agreement'
        : 'The orchestrator is off — read and set its operating agreement here';
}

export {
  loadOrch, renderOrchestrator, renderOrchStrip, renderOrchTab, setPath
};
