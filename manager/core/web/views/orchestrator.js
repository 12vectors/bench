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
import { ARM_MS, arming } from '../lib/arm.js';
import { $, esc } from '../lib/dom.js';
import { setHtml } from '../lib/keyed.js';
import { md } from '../lib/md.js';
import { activeSlot, askAttrs, openAgent, panelMounted, registerAgent, renderPanel, savePanel } from '../lib/panel.js';
import { pressHold } from '../lib/press.js';
import { admissionWords, ageWords, capacityWords, GROUP_WORDS, stepHere, orchHref, overall, RUN_WORDS, scopeName, SECTION_WORDS, SOURCE_WORDS, workGroup } from '../lib/orchwords.js';
import { cardId, findCard, ORCH_SECTIONS } from '../lib/route.js';
import { scheduleRender } from '../lib/schedule.js';
import { ago } from '../lib/time.js';
import { toast } from '../lib/toast.js';
import { nextStepHtml, OUTCOME_TONE, OUTCOME_WORDS } from '../lib/tracewords.js';

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
  resumeMigrated: 'The retired phase runner had halted or held this phase, so the upgrade held it back. Resuming hands it to its phase scope, which carries on from what the phase branch holds — an integrated member is never run again. Refused while anything still works it. Launches nothing itself; the next pass does, under your agreement.',
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

function stripHtml(section) {
  const f = facts();
  const st = overall(f);
  // never a link to the page already shown (task 147)
  const next = stepHere(st.word, section);
  const r = orch().reading;
  const nextHtml = !next ? ''
    : next.action === 'reload'
    ? btn('reload', next.label, { act: 'reload', primary: true })
    : next.href ? `<a class="o-btn primary" data-route data-o="next" href="${esc(next.href)}">${esc(next.label)}</a>`
      : `<a class="o-btn primary" data-route data-o="next" href="${esc(orchHref(next.section))}">${esc(next.label)}</a>`;
  const bench = scopeOf('bench');
  const pause = !r || !r.effective.configured || !r.effective.valid ? ''
    : bench && bench.paused ? btn('resume:bench', 'Resume all new work', { act: 'scope', data: { scope: 'bench', action: 'resume' } })
      : btn('pause:bench', 'Pause all new work', { act: 'scope', data: { scope: 'bench', action: 'pause' } });
  const at = orch().at ? `read ${ago(orch().at / 1000)} ago` : 'not read yet';
  // the shared conversation (task 205): asked about what this page shows
  const ask = `<button type="button" class="o-btn" data-o="ask-open"${askAttrs('orch', pageTarget())}>Ask orchestrator</button>`;
  return `<section class="o-status ${esc(st.tone)}" aria-labelledby="o-status-h">` +
    `<div class="o-statustext"><p class="o-eyebrow">Orchestrator · ${esc(at)}</p>` +
    `<h2 id="o-status-h">${badge(st.label, st.tone)}</h2>` +
    `<p>${esc(st.sentence)}</p>` +
    (r ? `<p class="o-dim">Agreement ${esc(r.effective.revision)}` +
      ` · ${esc(identityWords(r.identity))}</p>` : '') +
    `</div><div class="o-acts">${nextHtml}${pause}${ask}</div>` +
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
  // asking reads its own routes, so it does not wait on the reading
  if (section === 'ask') return askHtml();
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
    case 'trace': return traceHtml();
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
    offlineHtml() + stripHtml(section) + noticeHtml() + sectionHtml(section) + `</div>`;
}

/* ── ask orchestrator (task 181) ─────────────────────────────────────── */

/* Asking explains; it never acts. The section reads GET /api/ask (the
   conversations and what asking would launch and count) and one thread,
   and the only write it makes is POST /api/ask — a person's explicit,
   armed submission. Opening it, reloading it and reconnecting launch
   nothing. */

const ASK_STATUS = {
  valid: ['Answered', 'calm'],
  stale: ['Stale — the facts changed while it ran; history, not current', 'alarm'],
  invalid: ['Refused by validation — no answer kept', 'alarm'],
  failed: ['Failed — no answer', 'alarm'],
  cancelled: ['Cancelled — no answer', 'idle'],
  refused: ['Not launched', 'alarm'],
  queued: ['Queued', 'working'],
  running: ['Reasoning', 'working'],
  asked: ['Asked', 'neutral'],
};
const BY_WORDS = { you: 'you', owner: 'the product owner', orchestrator: 'the orchestrator, by itself',
                   teammate: 'a teammate', nobody: 'nobody yet' };
const CONTROL_WORDS = {
  'open-card': 'Open the card', 'move-card': 'Move it on the Board', 'start-work': '▸ start work on the card',
  hold: '‖ hold on the card', 'take-over': '▸ take over on the card', 'answer-decision': 'Answer in Decisions',
  'run-required-checks': 'Run required checks on the card', 'settle-operation': 'Settle it in Recovery',
  'resolve-incident': 'Resolve it in Recovery', 'reset-breaker': 'Reset the breaker in Recovery',
  'edit-agreement': 'Review the Operating agreement', 'adopt-phase': 'See the phase in Orchestrator → Phases',
  'designate-installation': 'Designate in Overview', 'settings-agents': 'Settings → Agents',
};

function askState() {
  const o = orch();
  if (!o.ask) {
    o.ask = { list: null, pre: null, at: 0, error: null, fetching: false, conv: null,
              thread: {}, scope: null, text: '', req: null, pollT: null, item: undefined };
  }
  return o.ask;
}

async function loadAsk(force) {
  const a = askState();
  if (a.fetching) return;
  if (!force && a.at && Date.now() - a.at < FRESH_MS) return;
  a.fetching = true;
  try {
    const body = await getJson('/api/ask');
    a.list = body.conversations || [];
    a.pre = body.preflight || null;
    a.actor = body.actor || null;
    a.error = null;
  } catch (e) {
    a.error = (e && e.message) || 'the conversations could not be read';
  } finally {
    a.at = Date.now();
    a.fetching = false;
  }
  scheduleRender();
}

async function loadThread(id, force) {
  const a = askState();
  if (!id) return;
  const have = a.thread[id];
  if (have && (have.fetching || (!force && Date.now() - have.at < FRESH_MS))) return;
  a.thread[id] = { ...(have || {}), fetching: true, at: (have || {}).at || 0 };
  try {
    const data = await getJson(`/api/ask/thread?conversation=${encodeURIComponent(id)}`);
    a.thread[id] = { data, error: null, at: Date.now(), fetching: false };
  } catch (e) {
    a.thread[id] = { ...(have || {}), error: (e && e.message) || 'not read', at: Date.now(), fetching: false };
  }
  // while a run answers, ask again in a few seconds — a read, never a launch
  const live = ((a.thread[id].data || {}).conversation || {}).exchanges || [];
  clearTimeout(a.pollT);
  if (live.some(x => x.active)) a.pollT = setTimeout(() => { loadThread(id, true); loadAsk(true); }, 3000);
  scheduleRender();
}

/* The target a page's question is about (task 205): the card or phase the
   address names, else the board. Inferred, never a form to fill in. */
function pageTarget() {
  const t = selected();
  return t ? { kind: t.isPhase ? 'phase' : 'card', card: String(idOf(t)) } : { kind: 'board' };
}

function scopeWords(scope) {
  if (!scope || scope.kind === 'board') return 'the whole board';
  const found = findCard(scope.card, S.state).task;
  const what = scope.kind === 'phase' ? 'Phase' : 'Card';
  return found ? `${what} ${num(found)} · ${title(found)}` : `${what} ${scope.card}`;
}

function convWhereWords(c) {
  const w = c.where || {};
  if ((c.scope || w.kind) === 'board') return 'Board';
  return `${w.kind === 'phase' ? 'Phase' : 'Card'} #${parseInt(w.card || c.subject, 10) || c.subject}`;
}

function scopeMatches(c, scope) {
  if (scope.kind === 'board') return (c.scope || (c.where || {}).kind) === 'board';
  const t = findCard(scope.card, S.state).task;
  return !!t && (c.where || {}).file === t.file;
}

function preflightHtml(pre, follow) {
  if (!pre) return `<p class="o-dim">Reading what asking would launch…</p>`;
  const model = pre.model ? `${pre.model} (${pre.modelSource === 'default' ? 'default' : esc(pre.modelSetting || pre.modelSource)})`
    : 'the harness’s own default model';
  return `<div class="o-askpre">` +
    `<p><strong>On submit:</strong> one read-only reasoning run through <strong>${esc(pre.harness)}</strong>` +
    ` · model ${model}. ${esc(pre.readOnly)}.</p>` +
    `<p class="o-dim">${esc(pre.accounting)} ${esc(pre.delegation)}</p>` +
    `<p class="o-dim">${follow ? 'A follow-up keeps this conversation and reads fresh facts on submission.'
      : 'Questions and replies are kept with the conversation — on the card, the phase card, or the board’s shared conversation file.'}</p>` +
    `</div>`;
}

/* What a question's text names besides its target (task 205): `#43`,
   `card 43`, `phase 44`. A reference that resolves uniquely to another
   card on this board is offered as an explicit switch — never applied by
   itself; one that names two cards is said, so nobody guesses. */
function referencesIn(text, target) {
  const nums = new Set();
  const re = /(?:#|\b(?:card|phase)\s+#?)(\d{1,5})\b/gi;
  let m;
  while ((m = re.exec(text || ''))) nums.add(String(parseInt(m[1], 10)));
  const here = target && target.kind !== 'board' ? findCard(target.card, S.state).task : null;
  const out = [];
  for (const n of nums) {
    const found = findCard(n, S.state);
    if (found.ambiguous) out.push({ n, ambiguous: found.ambiguous });
    else if (found.task && found.task !== here) out.push({ n, task: found.task });
    else if (!found.task) out.push({ n, missing: true });
  }
  return out.slice(0, 4);
}

function referencesHtml(s) {
  const refs = referencesIn(s.data.text, s.target);
  if (!refs.length) return '';
  return `<div class="ap-refs" role="note">` + refs.map(r => {
    if (r.missing) return `<p>Your question mentions #${esc(r.n)}, which is not a card on this board. It is asked about ${esc(scopeWords(s.target))} as written.</p>`;
    if (r.ambiguous) return `<p>#${esc(r.n)} names more than one card (${esc(r.ambiguous.join(', '))}); nothing is switched. Open the one you mean and ask from there.</p>`;
    const kind = r.task.isPhase ? 'phase' : 'card';
    return `<p>Your question mentions ${esc(num(r.task))} · ${esc(title(r.task))}. This conversation stays about ${esc(scopeWords(s.target))} unless you switch.</p>` +
      `<button type="button" class="o-btn" data-oact="paskRetarget" data-o="pask-ref-${esc(r.n)}" data-kind="${kind}" data-card="${esc(idOf(r.task))}">` +
      `Ask about ${esc(num(r.task))} instead</button>`;
  }).join('') + `</div>`;
}

/* The panel's composer: the question, the one blocker that stops it (if
   any), and Ask. What asking launches and counts is one fold away. */
function panelComposerHtml(s, thread) {
  const a = askState();
  const d = s.data;
  const pre = a.pre;
  const follow = !!(thread && d.conv);
  const busy = !!(thread && ((thread.conversation || {}).exchanges || []).some(x => x.active));
  const disabled = !pre || !pre.ready || busy;
  const why = busy ? 'a run is still answering this conversation; wait for it or cancel it' : (pre && pre.reason) || '';
  const blocker = pre && !pre.ready
    ? `<p class="ap-block" role="alert"><strong>Cannot ask here now:</strong> ${esc(pre.reason || pre.code)}` +
      (pre.remedy ? ` <span class="o-dim">Fix: ${esc(pre.remedy)}</span>` : '') + `</p>` : '';
  return `<div class="o-askcomposer ap-composer">` +
    `<label for="ap-otext" class="o-eyebrow">${follow ? 'Ask a follow-up' : `Ask about ${esc(scopeWords(s.target))}`}</label>` +
    `<textarea id="ap-otext" rows="3" maxlength="1000" data-ofield="pask-text" ` +
    `placeholder="${follow ? 'And what would unblock it?' : s.target.kind === 'board' ? 'What is consuming capacity?' : 'Why is this card still in To Do?'}">` +
    `${esc(d.text)}</textarea>` + referencesHtml(s) + blocker +
    `<div class="o-acts">` + btn(`pask:${s.key}`, follow ? 'Ask follow-up' : 'Ask', {
      act: 'pask', primary: true, disabled, why,
      confirm: 'Confirm: launch one read-only reasoning run' }) +
    (d.conv ? `<button type="button" class="o-btn" data-oact="paskNew" data-o="pask-new">New conversation</button>` : '') +
    `</div>` +
    `<details class="ap-tech" data-okeep="ap-tech"${keepOpen('ap-tech')}><summary>Technical details — model, access and accounting</summary>` +
    preflightHtml(pre, follow) +
    `<p class="o-effect">Asking explains; it acts on nothing. No card moves, no work starts or stops, no check runs, and no agreement, gate or budget changes. A suggestion in a reply is a link to where that control already lives.</p>` +
    `</details></div>`;
}

/* The panel's body: the conversation about the slot's target, or a note
   that there is none yet, then the composer. The newest conversation
   about the target is resumed when the slot has not chosen one. */
function panelBodyHtml(s) {
  const a = askState();
  loadAsk(false);
  const d = s.data;
  if (!d.conv && !d.chosen && a.list) {
    const hit = a.list.filter(c => scopeMatches(c, s.target))
      .sort((x, y) => String(y.updated || '').localeCompare(String(x.updated || '')))[0];
    d.chosen = true;
    if (hit) { d.conv = hit.id; savePanel(); }
  }
  if (d.conv) loadThread(d.conv, false);
  const entry = d.conv ? (a.thread[d.conv] || null) : null;
  const thread = entry && entry.data;
  return (a.error ? `<p class="o-notice alarm" role="alert">${esc(a.error)}</p>` : '') +
    (d.conv ? threadHtml(entry)
      : !a.list ? `<p class="o-dim" aria-busy="true">Reading earlier conversations…</p>`
        : `<p class="ap-empty">No conversation about ${esc(scopeWords(s.target))} yet. Ask below: one read-only reasoning run answers, from bench’s own records.</p>`) +
    panelComposerHtml(s, thread);
}

function slotTargetOf(c) {
  const w = c.where || {};
  if ((c.scope || w.kind) === 'board') return { kind: 'board' };
  return { kind: w.kind === 'phase' ? 'phase' : 'card', card: String(parseInt(w.card || c.subject, 10) || c.subject) };
}

function targetChoices(s) {
  const cur = s.target;
  const same = (t) => t.kind === cur.kind && String(t.card || '') === String(cur.card || '');
  const list = [{ target: { kind: 'board' }, label: 'The whole board' }];
  for (const t of tasks().filter(x => x.stage !== 'archive')) {
    const target = { kind: t.isPhase ? 'phase' : 'card', card: String(idOf(t)) };
    list.push({ target, label: `${t.isPhase ? 'Phase' : 'Card'} ${num(t)} · ${title(t)}` });
  }
  return list.slice(0, 400).map(x => ({ ...x, current: same(x.target) }));
}

/* The Orchestrator's adapter for the shared panel (task 205). Registered
   by the composition root; it draws through the routes task 181 owns. */
function registerOrchAgent() {
  registerAgent('orch', {
    label: 'Orchestrator',
    capability: 'Answers questions about the work; does not change it.',
    key: (t) => !t || t.kind === 'board' ? 'orch:board' : `orch:${t.kind}:${t.card}`,
    create: () => ({ conv: null, text: '', req: null, chosen: false }),
    about: (s) => scopeWords(s.target),
    targets: targetChoices,
    body: panelBodyHtml,
    opened: (s) => { loadAsk(false); if (s.data.conv) loadThread(s.data.conv, false); },
    persist: (s) => ({ conv: s.data.conv, text: s.data.text, req: s.data.req, chosen: s.data.chosen }),
    revive: (d) => ({ conv: typeof d.conv === 'string' ? d.conv : null, text: typeof d.text === 'string' ? d.text : '',
                      req: typeof d.req === 'string' ? d.req : null, chosen: !!d.chosen }),
    wire: wirePanel,
  });
}

/* The panel's own listeners: its buttons and its question field. Every
   button that sends is armed and goes through the route that owns it. */
function wirePanel(el) {
  el.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-oact]');
    if (!b || !el.contains(b) || b.disabled) return;
    const s = activeSlot();
    if (!s || s.purpose !== 'orch') return;
    e.preventDefault();
    panelAction(b, s);
  });
  const onField = (e) => {
    const f = e.target;
    if (!f.dataset || f.dataset.ofield !== 'pask-text') return;
    const s = activeSlot();
    if (!s || s.purpose !== 'orch') return;
    // a changed question is a new submission; the same text resent is not
    if (s.data.text !== f.value) s.data.req = null;
    s.data.text = f.value;
    if (e.type === 'change') { savePanel(); renderPanel(); }
  };
  el.addEventListener('input', onField);
  el.addEventListener('change', onField);
  el.addEventListener('toggle', (e) => {
    const k = e.target && e.target.dataset && e.target.dataset.okeep;
    if (k) orch().open[k] = e.target.open;
  }, true);
}

function panelAction(b, s) {
  const d = b.dataset;
  const key = d.o || d.oact;
  switch (d.oact) {
    case 'pask':
      return armed(key, () => submitPanel(s));
    case 'askCancel':
      return armed(key, async () => {
        const res = await send('/api/ask/cancel', { operation: d.operation }, false);
        if (s.data.conv) loadThread(s.data.conv, true);
        return res.ok ? { said: 'cancel requested — the reply records it once the run is observed gone; nothing else is touched' } : res;
      });
    case 'askRetry':
      return armed(key, async () => {
        const res = await send('/api/ask/retry', { conversation: s.data.conv, ask: d.ask }, false);
        if (s.data.conv) loadThread(s.data.conv, true);
        loadAsk(true);
        return res.ok ? { said: res.data.duplicate ? 'the inputs are unchanged — the earlier outcome stands' : 'asked again — one read-only reasoning run' }
          : { ok: false, error: res.error + (res.data && res.data.remedy ? ` — ${res.data.remedy}` : '') };
      });
    case 'paskNew':
      // a new conversation about the same target; the old one stays listed
      s.data.conv = null;
      s.data.chosen = true;
      s.data.req = null;
      savePanel();
      renderPanel(true);
      document.querySelector('#ap-otext')?.focus();
      return;
    case 'paskRetarget': {
      // an explicit switch: the question moves to the other target's
      // conversation, and this one is kept as it was
      const text = s.data.text;
      const to = openAgent('orch', { kind: d.kind, card: String(d.card) }, { focus: 'composer' });
      // moved only into an empty composer: text already typed there is
      // never written over, and then this one keeps its own
      if (to && to !== s && !to.data.text) {
        to.data.text = text; to.data.req = null;
        s.data.text = ''; s.data.req = null;
        savePanel(); renderPanel(true);
      }
      toast(`asking about ${scopeWords(to ? to.target : {})} — the conversation about ${scopeWords(s.target)} is kept`);
      return;
    }
    default:
  }
}

function refHref(ref, reply) {
  const [kind, name] = [ref.slice(0, ref.indexOf(':')), ref.slice(ref.indexOf(':') + 1)];
  if (kind === 'card') {
    const t = findCard(name, S.state).task;
    return t ? { href: `/cards/${encodeURIComponent(idOf(t))}`, label: `Card ${num(t)}`, route: true }
      : { href: null, label: `Card #${name} (not on this board)` };
  }
  if (kind === 'evidence') {
    const w = (reply && reply.subject) || '';
    return { href: w && w !== 'board' ? `/cards/${encodeURIComponent(w)}` : null,
             label: `Check evidence ${name}`, note: 'open the card, then its ◫ evidence' };
  }
  if (name === 'reason-facts' && reply && reply.operation) {
    return { href: `/api/ask/facts?operation=${encodeURIComponent(reply.operation)}`,
             label: `Bench’s readings${reply.snapshot ? ` at ${when(reply.snapshot)}` : ''}`, raw: true };
  }
  if (name === 'reason-card' && reply && reply.subject && reply.subject !== 'board') {
    return { href: `/cards/${encodeURIComponent(reply.subject)}`, label: `The card #${reply.subject}`, route: true };
  }
  return { href: null, label: `Input ${name}` };
}

function refLinks(refs, reply, notes) {
  return (refs || []).map(ref => {
    const r = refHref(ref, reply);
    if (!notes.find(n => n.ref === ref)) notes.push({ ref, ...r });
    const n = notes.findIndex(x => x.ref === ref) + 1;
    return r.href ? `<a href="${esc(r.href)}"${r.route ? ' data-route' : ' target="_blank" rel="noopener"'}` +
      ` title="${esc(r.label)}">[${n}]</a>` : `<span title="${esc(r.label)}">[${n}]</span>`;
  }).join(' ');
}

function replyHtml(reply, notes) {
  const [label, tone] = ASK_STATUS[reply.status] || [reply.status, 'neutral'];
  const stale = reply.status === 'valid' && reply.fresh && reply.fresh.fresh === false;
  const meta = [reply.agent, reply.harness, reply.model, reply.snapshot ? `facts read ${when(reply.snapshot)}` : null]
    .filter(Boolean).join(' · ');
  let body = '';
  if (reply.answer && (reply.status === 'valid' || reply.status === 'stale')) {
    const parts = [['now', 'What is happening now'], ['why', 'Why'],
                   ['next', 'What happens next by itself'], ['unblock', 'Who can unblock it']];
    body += `<p class="o-answerlead">${esc(reply.summary || '')}</p><dl class="o-answer">` +
      parts.map(([k, l]) => `<dt>${l}</dt><dd>${esc(reply.answer[k] || 'not said')}</dd>`).join('') + `</dl>`;
    if ((reply.findings || []).length) {
      body += `<h4>Recorded facts <span class="o-dim">— each cites where bench recorded it</span></h4><ul class="o-askfacts">` +
        reply.findings.map(f => `<li>${esc(f.statement)} ${refLinks(f.evidence, reply, notes)}</li>`).join('') + `</ul>`;
    }
    if ((reply.gaps || []).length) {
      body += `<h4>What the records do not say</h4><ul class="o-askgaps">` +
        reply.gaps.map(g => `<li>${esc(g.gap)}</li>`).join('') + `</ul>`;
    }
    if ((reply.suggestions || []).length) {
      body += `<h4>Suggestions <span class="o-dim">— not actions; nothing here runs</span></h4><ul class="o-asksugg">` +
        reply.suggestions.map(s => `<li>${esc(s.suggestion)} <span class="o-dim">· by ${esc(BY_WORDS[s.by] || s.by || 'nobody')}</span>` +
          `${suggestionLink(s, reply)} ${refLinks(s.evidence, reply, notes)}</li>`).join('') + `</ul>`;
    }
  } else {
    body += `<p>${esc(reply.summary || '')}</p>` +
      ((reply.diagnostics || []).length ? `<p class="o-dim">Codes: ${esc(reply.diagnostics.join(', '))}</p>` : '');
  }
  return `<article class="o-reply ${esc(tone)}" aria-label="Orchestrator reply">` +
    `<p class="o-eyebrow">Orchestrator · ${badge(stale ? 'Answered — no longer current' : label, stale ? 'alarm' : tone)}` +
    ` · <span title="a reasoning run's interpretation of bench's records">agent-reported interpretation</span>` +
    `${reply.at ? ` · ${esc(when(reply.at))}` : ''}</p>` +
    (stale ? `<p class="o-dim" role="note">Changed since: ${esc((reply.fresh || {}).why || 'its inputs')}. Read it as history; ask again for a current answer.</p>` : '') +
    body + (meta ? `<p class="o-dim">${esc(meta)}</p>` : '') + `</article>`;
}

function suggestionLink(s, reply) {
  const label = CONTROL_WORDS[s.control];
  if (!label) return '';
  const card = s.card || (reply.subject && reply.subject !== 'board' ? reply.subject : null);
  const section = { 'answer-decision': 'decisions', 'settle-operation': 'recovery', 'resolve-incident': 'recovery',
                    'reset-breaker': 'recovery', 'edit-agreement': 'agreement', 'adopt-phase': 'phases',
                    'designate-installation': 'overview' }[s.control];
  const href = s.control === 'settings-agents' ? '/settings/agents'
    : section ? orchHref(section, card || undefined)
      : card ? `/cards/${encodeURIComponent(card)}` : null;
  return href ? ` · <a data-route data-o="sugg-${esc(s.id)}" href="${esc(href)}">${esc(label)} ↗</a>` : '';
}

function activeHtml(x) {
  const j = x.active;
  const since = j.started || j.created;
  return `<div class="o-askactive" role="status" aria-live="polite">${badge(ASK_STATUS[j.status][0], 'working')} ` +
    `<span>${esc(j.name || 'A reasoning run')} · ${esc(j.harness || '')}${j.model ? ' · ' + esc(j.model) : ''}` +
    `${since ? ` · ${esc(ago(since))} so far` : ''}</span>` +
    `<p class="o-dim">A read-only reasoning run, not implementation: it reads bench’s facts and the cards, and nothing on the board changes while it runs. The board observes the process; it shows no invented steps.</p>` +
    `<div class="o-acts">${btn(`ask-cancel:${j.id}`, 'Cancel this question', { act: 'askCancel', data: { operation: j.id },
      confirm: 'Confirm: cancel this reasoning run' })}</div></div>`;
}

function threadHtml(entry) {
  if (entry && entry.error && !entry.data) return `<p class="o-empty" role="alert">The conversation could not be read: ${esc(entry.error)}.</p>`;
  if (!entry || !entry.data) return `<p class="o-empty" aria-busy="true">Reading the conversation…</p>`;
  const conv = entry.data.conversation;
  const notes = [];
  const where = entry.data.where || {};
  const html = conv.exchanges.map(x => {
    const q = x.ask;
    const replies = x.replies.map(r => replyHtml(r, notes)).join('');
    const retry = x.canRetry ? btn(`ask-retry:${q.id}`, 'Retry this question', { act: 'askRetry', data: { ask: q.id },
      confirm: 'Confirm: launch the question again' }) : '';
    return `<section class="o-exchange"><div class="o-askq"><p class="o-eyebrow">${esc(q.actor || 'You')} · ${esc(when(q.at))}</p>` +
      `<p>${esc(q.text)}</p></div>` + replies + (x.active ? activeHtml(x) : '') +
      (!x.active && !x.replies.length ? `<p class="o-dim">No run answered this question yet.</p>` : '') +
      (retry ? `<div class="o-acts">${retry}</div>` : '') + `</section>`;
  }).join('');
  const rail = notes.length ? `<aside class="o-askrail" aria-label="Evidence for these answers"><p class="o-eyebrow">Evidence</p><ol>` +
    notes.map(n => `<li>${n.href ? `<a href="${esc(n.href)}"${n.route ? ' data-route' : ' target="_blank" rel="noopener"'}>${esc(n.label)}</a>` : esc(n.label)}` +
      `${n.note ? ` <span class="o-dim">${esc(n.note)}</span>` : ''}</li>`).join('') +
    `</ol><p class="o-dim">Bench’s readings are a snapshot: each reply says when they were read.</p></aside>` : '';
  const kept = where.kind === 'board' ? 'the board’s shared conversation file, synced with the work state'
    : `card #${esc(where.subject)}’s Orchestration record`;
  return `<div class="o-askthread"><div class="o-askmain"><p class="o-dim">Kept in ${kept}` +
    `${entry.data.persistence && !entry.data.persistence.commits ? ' (written, not committed — moves do not commit on this board)' : ''}.</p>` +
    html + `</div>${rail}</div>`;
}

/* The Ask section (task 205): the conversations kept on this board, each
   opened in the shared panel, and one way to ask about what the address
   names. An address naming a card (`/orchestrator/ask?card=43`, kept from
   task 181) opens that card's conversation in the panel once per arrival:
   reading, never asking. */
function askHtml() {
  const a = askState();
  loadAsk(false);
  const here = pageTarget();
  // only once the panel is mounted (the composition root opens the
  // address it booted on itself; this is for a link followed later)
  if (S.orchItem && a.linked !== S.orchItem && S.state && panelMounted()) setTimeout(openAskAddress, 0);
  else if (!S.orchItem) a.linked = null;
  const list = a.list || [];
  const listHtml = !a.list ? `<p class="o-dim" aria-busy="true">Reading conversations…</p>`
    : !list.length ? `<p class="o-dim">No conversations yet. Ask the first question.</p>`
    : `<ul class="o-asklist">` + list.map(c => `<li><button type="button" class="o-askconv${scopeMatches(c, here) ? ' here' : ''}"` +
        ` data-oact="askConv" data-conv="${esc(c.id)}" data-o="conv-${esc(c.id)}">` +
        `<span>${esc(c.title || '(no question)')}</span><small>${esc(convWhereWords(c))} · ${esc(c.count)} question${c.count === 1 ? '' : 's'}` +
        ` · ${esc((ASK_STATUS[c.status] || [c.status || ''])[0])} · ${esc(when(c.updated))}</small></button></li>`).join('') + `</ul>`;
  return `<p class="o-lead">Ask why work is or is not moving. A question launches one read-only reasoning run on your request; the answer explains, from bench’s own records, and changes nothing. Conversations open in the panel beside the page, so the work stays in view.</p>` +
    (a.error ? `<p class="o-notice alarm" role="alert">${esc(a.error)}</p>` : '') +
    `<div class="o-acts"><button type="button" class="o-btn primary" data-o="ask-here"${askAttrs('orch', here)}>Ask about ${esc(scopeWords(here))}</button></div>` +
    `<section class="o-askhistory" aria-labelledby="o-askhist-h"><h2 id="o-askhist-h" class="o-eyebrow">Conversations on this board</h2>${listHtml}</section>`;
}

/* `/orchestrator/ask?card=<card>`: the card's conversation, opened in the
   panel once per arrival at the address. A read; nothing is asked. */
function openAskAddress() {
  if (S.view !== 'orchestrator' || S.orchSection !== 'ask' || !S.orchItem || !S.state) return false;
  const a = askState();
  if (a.linked === S.orchItem) return false;
  if (!selected()) return false;
  a.linked = S.orchItem;
  openAgent('orch', pageTarget(), { focus: 'composer' });
  return true;
}

/* One submission from a panel slot. Its request identity is kept with the
   slot — in this tab's storage too — before it is sent, so a resend after
   a lost reply, a double click or a reload is the same submission and the
   server answers it with the first outcome. */
async function submitPanel(s) {
  const d = s.data;
  const text = (d.text || '').trim();
  if (text.length < 3) return { ok: false, error: 'write a question first — nothing was sent' };
  d.req = d.req || newRequest('ask');
  savePanel();
  const body = { text, request: d.req };
  if (d.conv) body.conversation = d.conv;
  else body.scope = s.target.kind === 'board' ? { kind: 'board' } : { kind: s.target.kind, card: String(s.target.card) };
  let res = await send('/api/ask', body, false);
  // no answer at all: the same identity once more is answered from the record
  if (res.status === 0) res = await send('/api/ask', body, false);
  if (!res.ok) {
    if (res.data && res.data.code === 'not-launched') { d.text = ''; d.req = null; }
    savePanel();
    loadAsk(true);
    if (d.conv) loadThread(d.conv, true);
    return { ok: false, error: res.error + (res.data && res.data.remedy ? ` — ${res.data.remedy}` : '') };
  }
  d.text = '';
  d.req = null;
  d.conv = res.data.conversation;
  d.chosen = true;
  savePanel();
  loadAsk(true);
  loadThread(d.conv, true);
  return { said: res.data.duplicate ? 'already asked — the first submission stands' : 'asked — one read-only reasoning run is answering' };
}

/* ── overview ────────────────────────────────────────────────────────── */

function authorityHtml(scope) {
  return `<ul class="o-auth">` + Object.entries(scope.authority || {}).map(([word, a]) =>
    `<li class="${a.allowed ? 'yes' : 'no'}"><span class="o-glyph" aria-hidden="true">${a.allowed ? '✓' : '✕'}</span>` +
    `${esc(word)} <span class="o-dim">${a.allowed ? 'granted' : 'not granted'}` +
    `${a.by && a.by !== scope.id ? ` · decided by ${esc(scopeName(a.by))}` : ''}</span></li>`).join('') +
    `</ul>`;
}

// Task 182: shared capacity in words — the global total, what is in use
// on the board and free, and the scope's own ceiling and use. Nothing is
// called allocated or kept, because nothing is set aside.
function sharedCapacityHtml(scope) {
  const r = orch().reading;
  const usage = (r && r.usage) || {};
  const mine = (usage.running || {})[scope.id] || {};
  const totals = usage.totals || {};
  const rows = ['implementation', 'review', 'reasoning'].map(pool => {
    const cap = scope.capacity[pool] || {};
    const board = totals[pool] || 0;
    const free = Math.max(0, (cap.total || 0) - board);
    const label = pool === 'implementation' ? 'implementation slots (repairs too)' : `${pool} slots`;
    let words = `${board} of ${cap.total} in use on the board · ${free} available`;
    if (scope.kind === 'phase') {
      const from = cap.by === 'default' ? 'the default phase ceiling' : 'this phase’s own limit';
      words = `${mine[pool] || 0} running here · at most ${cap.ceiling} at once (${from}) · ${words}`;
    }
    const note = pool === 'reasoning' && !cap.total ? ' · no reasoning pool: diagnoses take implementation slots' : '';
    return `<div><dt>${esc(label)}</dt><dd>${esc(words + note)}</dd></div>`;
  });
  const daily = scope.capacity.dailyStarts || {};
  const startsBoard = usage.startsTodayTotal || 0;
  const startsHere = (usage.startsToday || {})[scope.id] || 0;
  rows.push(`<div><dt>launches today</dt><dd>${esc(scope.kind === 'bench'
    ? `${startsBoard} of ${daily.total} on the board`
    : `${startsHere} here, at most ${daily.ceiling} · ${startsBoard} of ${daily.total} on the board`)}` +
    ` <span class="o-dim">every launch counts once; a finished run refunds nothing</span></dd></div>`);
  const ceil = Object.entries(scope.ceilings || {}).map(([k, c]) =>
    `${k} ${c.value}${c.by !== scope.id ? ` (from ${scopeName(c.by)})` : ''}`).join(' · ');
  rows.push(`<div><dt>ceilings</dt><dd>${esc(ceil)}</dd></div>`);
  return `<dl class="o-kv">${rows.join('')}</dl>`;
}

// Which runs hold a slot now, whatever stage their card is in — a repair
// of a card in Review holds an implementation slot until it ends.
function holdersHtml() {
  const usage = (orch().reading || {}).usage || {};
  const list = usage.holders || [];
  if (!list.length) return '<p class="o-dim">No run holds a slot now.</p>';
  return `<ul class="o-list">${list.map(h => {
    const t = taskByFile(h.card);
    const what = h.kind === 'run' ? `${h.mode || 'work'} run${h.name ? ' ' + h.name : ''}` : `reserved by operation ${h.operation} (${h.status})`;
    return `<li>${t ? cardLink(t) : esc(h.card || '')} — ${esc(what)} · ${esc(h.pool)} slot` +
      `${h.stage ? ` · card in ${esc(h.stage)}` : ''}${h.scope ? ` · ${esc(scopeName(h.scope))}` : ' · no scope (a person’s own run)'}</li>`;
  }).join('')}</ul>`;
}

// The admission queue (task 182): who waits, in which order, since when,
// and any hold that keeps a place without blocking anyone.
function queueHtml() {
  const r = orch().reading || {};
  const q = r.queue || [];
  const body = q.length
    ? `<ol class="o-list">${q.map(e => {
      const t = taskByFile(e.file);
      const place = e.hold ? `on hold — ${e.hold} (keeps its place, blocks nobody)` : `#${e.position} in the ${e.pool} queue`;
      return `<li>${t ? cardLink(t) : esc(e.file || '')} — ${esc(e.action)} · ${esc(place)} · waiting since ${esc(when(e.readyAt))}` +
        `${e.scope ? ` · ${esc(scopeName(e.scope))}` : ''}</li>`;
    }).join('')}</ol>`
    : '<p class="o-dim">Nothing waits: every eligible request was admitted.</p>';
  return `<article class="o-panel"><h3>Capacity — first come, first served</h3>` +
    `<p class="o-dim">Implementation (repairs included), review and reasoning are each one pool for the whole board. An eligible request waits here from the moment it is ready and is admitted in that order, whatever its kind or priority, when its pool and its scope’s ceiling both have room. A request whose scope is full or on hold does not hold up the ones behind it.</p>` +
    `<h4>Waiting</h4>${body}<h4>Runs holding a slot</h4>${holdersHtml()}</article>`;
}

function capacityHtml(scope) {
  if (((scope.capacity || {}).implementation || {}).shared) return sharedCapacityHtml(scope);
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
    (scope.kind === 'phase' ? `<p class="o-dim">Strategy ${esc(scope.strategy || 'sequential')}. Authority is the intersection with the Bench scope; ` +
      (((scope.capacity || {}).implementation || {}).shared
        ? 'capacity is the board’s shared pools — this phase uses a slot only while a run holds it, up to its ceiling.'
        : 'capacity is allocated from its total, never duplicated (the reserved model — migrate it in the operating agreement).') + `</p>` : '') +
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
    (r.effective.migrationNeeded ? migrationNoticeHtml() : '') +
    (r.effective.capacityModel === 'shared' ? queueHtml() : '') +
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

/* Who holds a target's admission (task 189): the final update → check →
   admission sequence is serialized per target, and the plan names the one
   card holding each target and the cards waiting for it. Draws only. */
function admissionHtml(del) {
  const said = admissionWords(del, (file) => {
    const owner = taskByFile(file);
    return owner ? `${num(owner) || file} ${title(owner)}` : file;
  });
  return said ? `<span data-admission="${said.kind}">${badge(said.badge, said.tone)} ${esc(said.text)}</span>` : '';
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
    admissionHtml(del) +
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
  // only the work under way at the handover waits for adopting; a record
  // that did not say which (`adoptionPending` null) holds every working card
  const pending = inst.adoptionPending == null || inst.adoptionPending.includes(n);
  if (inst.adoptionRequired && n && t.stage !== 'to-do' && pending && !(inst.adoptedCards || []).includes(n)) {
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
    // the work-base assessment the schedule made (task 150): a read of
    // local refs; the decision itself is recorded on the card at launch
    (item && item.workBase ? `<div><dt>work base</dt><dd>${badge(item.workBase.decision || 'waiting', item.workBase.decision === 'blocked' ? 'alarm' : 'neutral')} ${esc(item.workBase.summary || '')} <span class="o-src">assessed by Bench</span></dd></div>` : '') +
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
    ? `Answered by ${esc(a.actor || 'a person')} ${when(a.at)}: ${esc(a.choice || a.note || '')}${a.choice && a.note ? ' — ' + esc(a.note) : ''}`
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
  // task 188: every phase is its phase scope's — nothing to adopt or
  // release. A phase the upgrade held because the retired runner had
  // halted or held it is resumed here, once, by a person
  if (p.resumable) {
    acts.push(action(`resume-phase:${n}`, 'Resume under the Orchestrator', 'phase', { card: p.card, action: 'resume' }, EFFECTS.resumeMigrated));
  }
  if (p.scope && r.effective.valid && !p.hold) {
    // a phase the agreement does not name can be paused too: the entry
    // written only narrows (enabled, paused)
    const paused = scope ? scope.paused : false;
    acts.push(paused
      ? action(`resume:${p.scope}`, 'Resume this phase', 'scope', { scope: p.scope, action: 'resume' }, EFFECTS.resumePhase)
      : action(`pause:${p.scope}`, 'Pause this phase', 'scope', { scope: p.scope, action: 'pause' }, EFFECTS.pausePhase));
  }
  const [word, tone] = p.hold ? ['Held by the upgrade', 'alarm']
    : scope ? scopeStateWords(scope)
    : r.effective.valid && r.effective.configured !== false ? ['Inherits the Bench scope', 'neutral'] : ['Orchestration off', 'idle'];
  const members = (p.members || []).map(m => {
    const mt = taskByFile(m.card);
    return `<li class="o-row">${mt ? cardLink(mt, num(mt) + ' ' + title(mt)) : esc(m.card)}` +
      `<span>${badge(m.state, m.state === 'working' ? 'working' : m.state === 'integrated' ? 'calm' : 'neutral')} ${esc(m.why || '')}</span>` +
      `<span class="o-dim">${esc(m.stage)}</span></li>`;
  });
  // the phase's lifecycle (task 160): the same reading the Board's face
  // and the Phases lane draw, so the three cannot disagree
  const life = p.lifecycle || null;
  const lifeTone = !life ? 'neutral'
    : ['unknown', 'moved-early', 'stopped', 'migrating', 'blocked'].includes(life.word) ? 'alarm'
    : life.word === 'working' ? 'working'
    : ['delivered', 'finishing'].includes(life.word) ? 'calm' : 'neutral';
  const who = life && life.next ? ({ you: 'You', agent: 'The agent', board: 'The board' }[life.next.who] || '') : '';
  const b = life && life.budget;
  const lifeHtml = life
    ? `<div class="o-life" data-life="${esc(life.word)}"><p>${badge(life.label, lifeTone)} ${esc(life.line)}</p>` +
      (life.next ? `<p><b>Next${who ? ' — ' + esc(who) : ''}:</b> ${esc(life.next.text)}</p>` : '') +
      ((life.inherited || []).length ? `<p class="o-dim">Queued with the phase, stored in Backlog: ${esc(life.inherited.map(f => {
        const bt = taskByFile(f); return bt ? num(bt) : f; }).join(', '))}. Each starts in its turn and moves to In Progress when it does; nobody needs to move it.</p>` : '') +
      (life.compat ? `<p class="o-warn">${badge('Older board', 'alarm')} ${esc(life.compat)}</p>` : '') +
      (b ? `<p class="o-dim">Budget: ${esc(b.lines.join('; '))}.</p>` +
        (b.missing || []).map(m => `<p class="o-warn">${badge('Missing', 'alarm')} ${esc(m)}</p>`).join('') +
        (b.warnings || []).map(w => `<p class="o-warn">${badge('Note', 'neutral')} ${esc(w)}</p>`).join('') : '') +
      `</div>`
    : '';
  return `<article class="o-panel o-phase${isSel ? ' sel' : ''}" id="ophase-${esc(n)}" aria-label="Phase ${esc(n)}">` +
    `<h3>${t ? cardLink(t, num(t)) : esc('#' + n)} ${esc(title(t))} ${badge(word, tone)}</h3>` +
    lifeHtml +
    `<dl class="o-kv"><div><dt>scheduling owner</dt><dd>${esc(p.owner)}${p.mode ? ` · mode ${esc(p.mode)}` : ''}</dd></div>` +
    `<div><dt>agreement</dt><dd>${p.named ? `an entry narrows it (${esc(p.scope)}) · strategy ${esc(p.strategy || 'sequential')}` : `no entry — it inherits the Bench scope · strategy ${esc(p.strategy || 'sequential')}`}</dd></div>` +
    (p.hold ? `<div><dt>upgrade hold</dt><dd>${esc(p.hold.reason || '')}</dd></div>` : '') +
    `<div><dt>integrated</dt><dd>${(p.integrated || []).length ? esc((p.integrated || []).map(x => '#' + x).join(', ')) + ' — in the phase branch, not delivered' : 'none yet'}</dd></div>` +
    (p.delivered !== undefined ? `<div><dt>delivered</dt><dd>${p.delivered ? 'yes' : 'not yet'}</dd></div>` : '') +
    `</dl>` +
    (scope ? `<details class="o-fold" data-okeep="pauth-${esc(n)}"${keepOpen('pauth-' + n, isSel)}><summary>Effective authority and capacity (what both the Bench scope and this phase grant)</summary>${authorityHtml(scope)}${capacityHtml(scope)}</details>` : '') +
    (members.length ? `<h4>Members</h4><ul class="o-rows">${members.join('')}</ul>` : '') +
    (acts.length ? `<h4>Actions</h4><div class="o-actions">${acts.join('')}</div>` : '') +
    // the phase's work lives on the Board (task 159): Board › Phases › it
    `<p><a data-route data-o="board-phase" href="/phases/${esc(n)}">View this phase in Board › Phases</a></p></article>`;
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
    `<p class="o-dim">Each side effect has an intent written before it and a receipt after; the same records are on the card they are about. ` +
    `Why each was chosen — and why other work waited — is in the <a data-route href="${esc(orchHref('trace'))}">Decision trace</a>.</p>` +
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

/* ── the decision trace (task 184) ───────────────────────────────────── */

/* Every scheduling pass this installation evaluated, newest first: what
   each candidate was read as, the rule and reason, what was selected and
   what became of it. Read from `GET /api/orchestration/trace` and, for an
   opened entry, `…/trace/entry` — reads only: opening, filtering,
   expanding and paging launch, reserve and write nothing. */

const NO_FILTERS = { card: '', scope: '', outcome: '', action: '', since: '', until: '' };
const TRACE_ACTIONS = ['implement', 'review', 'repair', 'verify', 'merge', 'integrate', 'phase', 'recover'];
const TRACE_OUTCOMES = ['action', 'no-action', 'launched', 'refused', 'unknown', 'waiting', 'blocked',
  'held', 'skipped', 'error'];

function traceState() {
  const o = orch();
  if (!o.trace) {
    o.trace = { filters: { ...NO_FILTERS }, draft: { ...NO_FILTERS }, key: null, entries: [],
                next: null, meta: null, total: 0, error: null, errorAt: 0, at: 0,
                fetching: false, entry: {} };
  }
  return o.trace;
}

function traceParams(t, before) {
  const f = t.filters;
  const q = new URLSearchParams();
  const card = S.orchItem || f.card;
  if (card) q.set('card', String(card).replace(/^#/, ''));
  for (const k of ['scope', 'outcome', 'action', 'since']) if (f[k]) q.set(k, f[k]);
  // a date alone means the whole of that day
  if (f.until) q.set('until', /^\d{4}-\d{2}-\d{2}$/.test(f.until) ? `${f.until}T23:59:59Z` : f.until);
  if (before) q.set('before', String(before));
  q.set('limit', '25');
  return q.toString();
}

async function loadTrace(force, older) {
  const t = traceState();
  const key = traceParams(t);
  if (t.fetching) return;
  if (!older && !force && t.key === key && (t.error ? Date.now() - t.errorAt < FRESH_MS
                                                    : Date.now() - t.at < FRESH_MS)) return;
  t.fetching = true;
  const same = t.key === key;
  try {
    const body = await getJson('/api/orchestration/trace?' + traceParams(t, older ? t.next : null));
    const tr = body.trace || {};
    const got = tr.entries || [];
    if (older && same) {
      t.entries = t.entries.concat(got);
      t.next = tr.next;
    } else if (same && t.entries.length) {
      // a refresh keeps the older pages already read: newer entries are
      // merged in by id, so nothing a person paged to disappears
      const byId = new Map(t.entries.map(e => [e.id, e]));
      for (const e of got) byId.set(e.id, e);
      t.entries = [...byId.values()].sort((a, b) => b.seq - a.seq);
      if (t.next === null || t.next === undefined) t.next = tr.next;
    } else {
      t.entries = got;
      t.next = tr.next;
    }
    t.meta = tr.meta || null;
    t.total = tr.total || 0;
    t.key = key;
    t.error = null;
    t.at = Date.now();
  } catch (e) {
    t.error = (e && e.message) || 'the trace could not be read';
    t.errorAt = Date.now();
    t.key = key;
  } finally {
    t.fetching = false;
  }
  scheduleRender();
}

async function loadTraceEntry(id) {
  const t = traceState();
  const have = t.entry[id];
  if (have && (have.fetching || (have.at && Date.now() - have.at < FRESH_MS) ||
               (have.error && Date.now() - have.errorAt < FRESH_MS))) return;
  t.entry[id] = { ...(have || {}), fetching: true };
  try {
    const res = await fetch(`/api/orchestration/trace/entry?id=${encodeURIComponent(id)}`);
    const body = await res.json().catch(() => ({}));
    if (res.status === 404) t.entry[id] = { missing: true, error: body.error, at: Date.now() };
    else if (!res.ok) throw new Error(body.error || `the board answered ${res.status}`);
    else t.entry[id] = { data: body.entry, links: body.links || {}, at: Date.now() };
  } catch (e) {
    t.entry[id] = { ...(have || {}), fetching: false, error: (e && e.message) || 'not read',
                    errorAt: Date.now() };
  }
  scheduleRender();
}

function utc(iso) {
  if (!iso) return 'time not recorded';
  return `${String(iso).replace('T', ' ').replace('Z', '')} UTC`;
}
function stamp(iso) {
  return `<time datetime="${esc(iso || '')}" title="${esc(utc(iso))}">${esc(utc(iso))}` +
    `${iso ? ` <span class="o-dim">(${when(iso)})</span>` : ''}</time>`;
}
function durationWords(s) {
  if (s === null || s === undefined) return '';
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}
function outcomeBadge(word) {
  return badge(OUTCOME_WORDS[word] || word || 'recorded', OUTCOME_TONE[word] || 'neutral');
}
function decCard(d) {
  const t = d.card ? taskByFile(d.card) : null;
  if (t) return cardLink(t);
  return d.number ? `#${esc(d.number)}` : esc(d.card || (d.kind === 'recovery' ? 'recovery' : 'the board'));
}
function factValue(v) {
  if (v === null || v === undefined || v === '') return '<span class="o-dim">not recorded</span>';
  if (typeof v === 'object') return `<code>${esc(JSON.stringify(v))}</code>`;
  return esc(String(v));
}

const FACT_WORDS = {
  basis: 'brief revision decided on', head: 'commit (candidate head)', priority: 'priority',
  dependsOn: 'depends on', touches: 'declared Touches', queue: 'admission queue',
  workBase: 'work base', overlap: 'Touches overlap', overlapUnknownWith: 'overlap unknown with',
  request: 'request id', title: 'title at this pass', unit: 'delivery unit', authority: 'authority',
  admitted: 'admitted',
};

function factsHtml(d) {
  const f = d.facts || {};
  const rows = [['scope', scopeName(d.scope)], ['stage', d.stage || null],
                ...Object.entries(f).filter(([k]) => k !== 'title')];
  if (!rows.length) return '<p class="o-dim">No facts were captured for this decision.</p>';
  return `<dl class="o-kv">${rows.map(([k, v]) =>
    `<div><dt>${esc(FACT_WORDS[k] || k)}</dt><dd>${factValue(v)}</dd></div>`).join('')}</dl>` +
    `<p class="o-dim">Read when the pass decided. A fact not listed was not captured — unknown, not absent.</p>`;
}

function linkHtml(d, links) {
  const out = [];
  const t = d.card ? taskByFile(d.card) : null;
  if (t) {
    const id = idOf(t);
    out.push(`<a data-route href="/cards/${esc(encodeURIComponent(id))}?tab=activity">Card history (the shared record)</a>`);
    out.push(`<a data-route href="${esc(orchHref('trace', id))}">Every decision about ${esc(num(t))}</a>`);
  }
  const r = d.result || {};
  const key = r.key || r.operation;
  if (key) {
    const op = links && links.operations ? links.operations[key] : undefined;
    const now = op === undefined ? 'open the entry to read its status now'
      : op === null ? 'not in the ledger now (retention, or another board)'
        : `${op.status}${op.outcome ? ' · ' + op.outcome : ''} now`;
    out.push(`operation <code>${esc(key)}</code> — <span class="o-dim">ledger: ${esc(now)}</span>`);
    if (r.agent) out.push(`run <code>${esc(r.agent)}</code> <a data-route href="/focus/${esc(encodeURIComponent(r.agent))}">Focus</a>`);
  }
  if (r.incident) {
    const inc = links && links.incidents ? links.incidents[r.incident] : undefined;
    const jobs = inc && inc.attempts ? inc.attempts.filter(a => a.job) : [];
    out.push(`incident <code>${esc(r.incident)}</code>${inc ? ` — ${esc(inc.status)} now` : ''} · ` +
      `<a data-route href="${esc(orchHref('recovery'))}">Recovery</a>`);
    for (const a of jobs) {
      out.push(`diagnosis ${esc(a.n)}: reasoning job <code>${esc(a.job)}</code> (${esc(a.status || '')}) — ` +
        `its response is <strong>agent-reported</strong>, a recommendation bench validated, not an observed result`);
    }
  }
  return out.length ? `<ul class="o-list">${out.map(x => `<li>${x}</li>`).join('')}</ul>`
    : '<p class="o-dim">No linked operation, run or incident.</p>';
}

function decisionHtml(d, entry, links) {
  const same = (entry.decisions || []).filter(x => x !== d && x.pool && x.pool === d.pool);
  const title = d.facts && d.facts.title ? ` <span class="o-dim">${esc(String(d.facts.title).replace(/^#?\d+\s*[—–-]\s*/, ''))}</span>` : '';
  const head = `<h4>${decCard(d)}${title} — ${esc(d.action || '')} ${outcomeBadge(d.outcome)}</h4>`;
  let what;
  if (d.selected) {
    const r = d.result;
    what = `<p><strong>Selected this pass</strong> (${esc(d.state || 'chosen')}). ` +
      (r ? `Then: ${outcomeBadge(d.outcome)}${r.reason ? ' — ' + esc(r.reason) : ''}` +
           `${d.outcome === 'refused' ? ' <span class="o-dim">(revalidated at execution; the selection above is what planning decided)</span>' : ''}`
         : d.heldBy ? `Not executed: held by ${esc(d.heldBy)}.` : 'No side effect followed in this pass.') + `</p>`;
  } else {
    what = `<p><strong>${esc(OUTCOME_WORDS[d.outcome] || 'Not selected')}</strong>: ${esc(d.reason || 'no reason recorded')}</p>`;
  }
  const alternatives = d.selected
    ? same.filter(x => !x.selected)
    : same.filter(x => x.selected);
  // each fold keeps its open state across a live repaint
  const at = `tf-${entry.id}-${(entry.decisions || []).indexOf(d)}`;
  const altHtml = !d.pool ? ''
    : traceFold(`${at}-alt`, `${d.selected ? 'Alternatives skipped' : 'Selected instead'} (${alternatives.length})`,
      alternatives.length ? `<ul class="o-list">${alternatives.map(x =>
        `<li>${decCard(x)} — ${esc(x.action)} · ${outcomeBadge(x.outcome)} <code>${esc(x.code || '')}</code>: ${esc(x.reason || '')}</li>`).join('')}</ul>`
        : `<p class="o-dim">${d.selected ? 'No other candidate for this pool was evaluated this pass.' : 'Nothing in this pool was selected this pass.'}</p>`);
  return `<article class="o-tdec${d.selected ? ' selected' : ''}">${head}${what}` +
    nextStepHtml(d, (s) => orchHref(s)) +
    traceFold(`${at}-facts`, 'Facts considered', factsHtml(d)) +
    altHtml +
    traceFold(`${at}-rule`, 'Rule or limit', `<p><code>${esc(d.code || 'none recorded')}</code>` +
      `${d.state ? ` · state <code>${esc(d.state)}</code>` : ''} — ${esc(d.reason || '')}</p>`) +
    traceFold(`${at}-links`, 'Evidence and links', linkHtml(d, links)) +
    `</article>`;
}

function traceFold(key, summary, body) {
  const open = orch().open[key];
  return `<details class="o-fold" data-okeep="${esc(key)}"${open ? ' open' : ''}>` +
    `<summary>${esc(summary)}</summary>${body}</details>`;
}

function entrySummary(e) {
  const decs = e.decisions || [];
  const acted = decs.filter(d => d.result);
  const cards = [...new Set((acted.length ? acted : decs).map(d => d.number ? `#${parseInt(d.number, 10)}` : null)
    .filter(Boolean))].slice(0, 4);
  const scopes = [...new Set(decs.map(d => d.scope).filter(Boolean))].map(scopeName);
  const what = e.status === 'skipped' ? 'Not evaluated'
    : e.status === 'error' ? 'Error — partial pass'
      : acted.length ? acted.slice(0, 2).map(d => `${d.action} ${d.number ? '#' + parseInt(d.number, 10) : ''}`).join(', ')
        : 'Wait / No action';
  const occ = e.occurrences || {};
  const times = occ.count > 1 ? ` <span class="o-badge neutral">×${esc(occ.count)} identical passes</span>` : '';
  return `<span class="o-src">${stamp(occ.last || e.at)}</span> ` +
    `<span class="o-tscope">${esc(scopes.join(', ') || 'no scope')}${cards.length ? ' · ' + esc(cards.join(' ')) : ''}</span> ` +
    `<strong>${esc(what)}</strong> — <span>${esc(e.headline || e.reason || '')}</span> ${outcomeBadge(e.outcome)}${times}`;
}

function entryBody(e, cached) {
  const links = cached && cached.links;
  const full = (cached && cached.data) || e;
  const occ = full.occurrences || {};
  const notes = [];
  if (e.restartBefore) notes.push('The board restarted before this pass.');
  if (e.quietBefore) notes.push(`No pass was recorded for ${durationWords(e.quietBefore)} before this one: the board was stopped, orchestration was off, or no agreement was in force.`);
  if (e.seqGapBefore) notes.push(`${e.seqGapBefore} pass record(s) are missing before this one — a recording gap, not a quiet board.`);
  if (e.gapBefore) notes.push(`${e.gapBefore.failures} pass(es) since ${utc(e.gapBefore.since)} could not be recorded (${e.gapBefore.error || 'no detail'}): the trace has a gap there.`);
  if (e.truncated) notes.push(`This record was bounded: ${JSON.stringify(e.truncated)} left out.`);
  const occHtml = occ.count > 1
    ? `<p>This exact decision was made on <strong>${esc(occ.count)} consecutive passes</strong>, from ${stamp(occ.first)} to ${stamp(occ.last)} (passes ${esc(occ.firstSeq)}–${esc(occ.lastSeq)}). Repeats are folded, never dropped.</p>` +
      (occ.passes ? traceFold(`tf-${full.id}-passes`, 'Every pass', `<ul class="o-list">${occ.passes.map(p =>
        `<li><code>${esc(p.id)}</code> · ${esc(utc(p.at))}</li>`).join('')}</ul>` +
        `${occ.passesOmitted ? `<p class="o-dim">${esc(occ.passesOmitted)} older pass ids are counted above and not listed.</p>` : ''}`) : '')
    : '';
  const decs = (full.decisions || []).slice().sort((a, b) =>
    (b.result ? 2 : b.selected ? 1 : 0) - (a.result ? 2 : a.selected ? 1 : 0));
  const want = S.orchItem;
  const shown = want ? decs.filter(d => String(d.number || '').replace(/^0+/, '') === String(want).replace(/^0+/, '') ||
                                         d.card === want || String(d.card || '').startsWith(`${want}-`)) : decs;
  const rest = decs.length - shown.length;
  const observed = (full.observed || []).concat(full.reconciled || []);
  return `<div class="o-tbody">` +
    (notes.length ? `<ul class="o-list o-tnotes">${notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : '') +
    `<p class="o-dim">Pass <code>${esc(full.id)}</code>${full.seconds !== undefined ? ` · ran ${esc(full.seconds)}s` : ''}` +
    `${full.owner && full.owner.role ? ` · installation ${esc(full.owner.role)}` : ''}` +
    `${full.agreement && full.agreement.revision ? ` · agreement ${esc(full.agreement.revision)}` : ''}` +
    ` · <a data-route data-o="entry-link" href="${esc(orchHref('trace', S.orchItem, full.id))}">Link to this entry</a></p>` +
    occHtml +
    (full.status === 'skipped' ? `<p><strong>Not evaluated:</strong> ${esc(full.reason || full.why || '')}. ` +
      `No card was read and nothing was decided on this pass.</p>` : '') +
    (full.status === 'error' ? `<p role="alert"><strong>The pass stopped with an error:</strong> ${esc(full.error || '')}. ` +
      `What it had read before is below; nothing after the error happened in this pass.</p>` : '') +
    (full.breaker && full.breaker.open ? `<p>The circuit breaker was open: no launch was attempted.</p>` : '') +
    (observed.length ? traceFold(`tf-${full.id}-observed`, `Observed this pass (${observed.length})`, `<ul class="o-list">${observed.map(o =>
      `<li><code>${esc(o.key || '')}</code> ${esc(o.action || '')} ${esc(o.file || o.card || '')} → ${esc(o.status || '')}${o.outcome ? ' · ' + esc(o.outcome) : ''}</li>`).join('')}</ul>`) : '') +
    (full.capacity && full.capacity.limits ? traceFold(`tf-${full.id}-capacity`, 'Capacity as read', factValue(full.capacity)) : '') +
    (shown.length ? shown.map(d => decisionHtml(d, full, links)).join('')
      : full.status === 'evaluated' ? `<p class="o-dim">${want ? `Card #${esc(want)} was not a candidate in this pass.` : 'No card in To Do, In Progress or Review was under an orchestrator scope on this pass.'}</p>` : '') +
    (rest > 0 ? `<p class="o-dim">${rest} other decision(s) in this pass are about other cards. <a data-route href="${esc(orchHref('trace', null, full.id))}">Show the whole pass</a></p>` : '') +
    (cached && cached.fetching ? '<p class="o-dim" aria-busy="true">Reading what its operations say now…</p>' : '') +
    (cached && cached.error && !cached.missing ? `<p class="o-dim" role="alert">Its links could not be read now: ${esc(cached.error)}</p>` : '') +
    `</div>`;
}

// `linked`: drawn as the entry an address names, which may be one of its
// folded repeats' ids — so it is open whatever id brought it here
function traceEntryHtml(e, linked = false) {
  const t = traceState();
  const okey = `tr-${e.id}`;
  const open = orch().open[okey] || linked || S.orchEntry === e.id;
  if (open) loadTraceEntry(e.id);
  return `<li class="o-trace-item"><details class="o-tentry" data-okeep="${esc(okey)}" data-o="${esc(okey)}"${open ? ' open' : ''}>` +
    `<summary>${entrySummary(e)}</summary>${open ? entryBody(e, t.entry[e.id]) : ''}</details></li>`;
}

function traceFiltersHtml() {
  const t = traceState();
  const d = t.draft;
  const r = orch().reading;
  const scopes = r && r.effective ? (r.effective.scopes || []).map(s => s.id) : [];
  const opt = (v, label, cur) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(label)}</option>`;
  const ctx = S.orchItem ? findCard(S.orchItem, S.state) : null;
  const ctxTask = ctx && ctx.task;
  const cardCtx = S.orchItem
    ? `<p class="o-tctx">Showing the decisions about card #${esc(S.orchItem)}. ` +
      (ctxTask ? `<a data-route href="/cards/${esc(encodeURIComponent(idOf(ctxTask)))}">Back to ${esc(num(ctxTask))}</a> · ` +
        `<a data-route href="/cards/${esc(encodeURIComponent(idOf(ctxTask)))}?tab=activity">Its history</a> · ` : '') +
      `<a data-route data-o="trace-all-cards" href="${esc(orchHref('trace'))}">Every card</a></p>` : '';
  return `<div class="o-tfilters" role="group" aria-label="Filter the decision trace">` +
    (S.orchItem ? '' : `<label>Card <input type="text" inputmode="numeric" data-ofield="tf" data-k="card" value="${esc(d.card)}" placeholder="any" size="6"></label>`) +
    `<label>Scope <select data-ofield="tf" data-k="scope">${opt('', 'any scope', d.scope)}` +
    `${scopes.map(s => opt(s, scopeName(s), d.scope)).join('')}</select></label>` +
    `<label>Outcome <select data-ofield="tf" data-k="outcome">${opt('', 'any outcome', d.outcome)}` +
    `${TRACE_OUTCOMES.map(w => opt(w, OUTCOME_WORDS[w] || w, d.outcome)).join('')}</select></label>` +
    `<label>Action <select data-ofield="tf" data-k="action">${opt('', 'any action', d.action)}` +
    `${TRACE_ACTIONS.map(w => opt(w, w, d.action)).join('')}</select></label>` +
    `<label>From <input type="date" data-ofield="tf" data-k="since" value="${esc(d.since)}"></label>` +
    `<label>To <input type="date" data-ofield="tf" data-k="until" value="${esc(d.until)}"></label>` +
    `<span class="o-acts"><button type="button" class="o-btn primary" data-oact="trace-apply" data-o="trace-apply">Apply filters</button>` +
    `<button type="button" class="o-btn" data-oact="trace-clear" data-o="trace-clear">Clear</button>` +
    `<button type="button" class="o-btn" data-oact="trace-reload" data-o="trace-reload">Refresh</button></span>` +
    `</div>` + cardCtx;
}

function traceMetaHtml(meta) {
  const m = meta || {};
  const lp = m.lastPass;
  const la = m.lastAction;
  const integ = m.integrity || {};
  const rec = m.recording || {};
  const rows = [
    ['Last pass evaluated', lp ? `${stamp(lp.at)} · ${esc(OUTCOME_WORDS[lp.outcome] || lp.outcome)}` : 'none recorded yet'],
    ['Last pass that acted', la ? stamp(la.at) : 'none recorded'],
    ['Scheduler', m.interval ? `a pass every ${esc(m.interval)}s while an agreement is in force and this installation executes` : 'interval unknown'],
    ['Kept', 'on this installation only — never committed or synced. The shared record of an action is the card’s own history.'],
    ['Covers', m.started ? `from ${stamp(m.started)}; nothing earlier was recorded, and nothing is reconstructed` : 'nothing yet'],
  ];
  if (m.boundary) rows.push(['Retention', `older passes (through pass ${esc(m.boundary.throughSeq)}${m.boundary.throughAt ? ', ' + esc(utc(m.boundary.throughAt)) : ''}) were pruned: the trace keeps the newest ${esc((m.retention || {}).keep)} segments`]);
  const warn = [];
  if (integ.malformedCount) warn.push(`${integ.malformedCount} unreadable line(s) were skipped (${(integ.malformed || []).slice(0, 3).map(x => `${x.segment}:${x.line}`).join(', ')}).`);
  if ((integ.gaps || []).length) warn.push(`${integ.gaps.reduce((n, g) => n + g.missing, 0)} pass record(s) are missing in ${integ.gaps.length} place(s).`);
  if (integ.orphanedRepeats) warn.push(`${integ.orphanedRepeats} repeat(s) name a record no longer kept.`);
  if (rec.failures) warn.push(`${rec.failures} pass(es) could not be recorded since ${utc(rec.since)}: ${rec.lastError || ''}. Scheduling was not affected.`);
  return `<article class="o-panel"><h3>What the trace records</h3>` +
    `<p>Every scheduling pass, as it was decided: the candidates the scheduler read, the rule and reason for each, what it selected and what then happened. ` +
    `A pass is <strong>deterministic</strong> and asks no model. Where a pass asked for a bounded reasoning job (a recovery diagnosis), its public response is linked and labelled <strong>agent-reported</strong> — a recommendation, never an observed result. ` +
    `What actually ran is the operation ledger’s and the card’s, linked from each decision.</p>` +
    `<dl class="o-kv">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>` +
    (warn.length ? `<div class="o-twarn" role="status"><strong>The trace is incomplete here:</strong><ul class="o-list">${warn.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : '') +
    `</article>`;
}

function traceHtml() {
  const t = traceState();
  loadTrace(false);
  if (S.orchEntry) loadTraceEntry(S.orchEntry);
  const linked = S.orchEntry ? t.entry[S.orchEntry] : null;
  let linkedHtml = '';
  if (S.orchEntry) {
    const e = linked && linked.data;
    linkedHtml = `<article class="o-panel" aria-labelledby="o-tlinked"><h3 id="o-tlinked">Linked entry</h3>` +
      (e ? `<ul class="o-trace">${traceEntryHtml(e, true)}</ul>`
        : linked && linked.missing ? `<p role="alert">No entry <code>${esc(S.orchEntry)}</code> on this installation — it may have been pruned by retention, or recorded by another board.</p>`
          : linked && linked.error ? `<p role="alert">The entry could not be read: ${esc(linked.error)}.</p>`
            : `<p class="o-dim" aria-busy="true">Reading the entry…</p>`) +
      `<p><a data-route href="${esc(orchHref('trace', S.orchItem))}">Close the linked entry</a></p></article>`;
  }
  let list;
  if (!t.at && !t.error) list = `<p class="o-empty" aria-busy="true">Reading the decision trace…</p>`;
  else if (t.error && !t.entries.length) {
    list = `<p class="o-empty" role="alert">The decision trace could not be read: ${esc(t.error)}. ` +
      `<button type="button" class="o-link" data-oact="trace-reload" data-o="trace-retry">Try again</button></p>`;
  } else if (!t.entries.length) {
    const started = t.meta && t.meta.started;
    const filtered = S.orchItem || Object.values(t.filters).some(Boolean);
    list = `<p class="o-empty">${filtered && started ? 'No recorded pass matches these filters.'
      : started ? 'No pass is recorded.'
        : 'No pass has been recorded on this installation yet. The trace starts when the scheduler first evaluates a pass with an operating agreement in force; nothing earlier is invented.'}</p>`;
  } else {
    list = `<ol class="o-trace" aria-label="Scheduling passes, newest first">${t.entries.map(e => traceEntryHtml(e)).join('')}</ol>` +
      (t.next ? `<p><button type="button" class="o-btn" data-oact="trace-older" data-o="trace-older"${t.fetching ? ' disabled' : ''}>Show older passes</button></p>`
        : `<p class="o-dim">That is every recorded pass${t.meta && t.meta.boundary ? ' still kept' : ''}.</p>`);
    if (t.error) list = `<p class="o-dim" role="alert">The newest reading failed (${esc(t.error)}); these are from the last one.</p>` + list;
  }
  return linkedHtml + traceMetaHtml(t.meta) + `<article class="o-panel"><h3>Passes, newest first</h3>` +
    traceFiltersHtml() + list + `</article>`;
}

/* ── the operating agreement ─────────────────────────────────────────── */

const AUTHORITY_WORDS = {
  readiness: 'Check readiness', reasoning: 'Launch reasoning agents',
  implement: 'Start committed work', review: 'Request checks and independent review',
  repair: 'Repair within the ceiling', recovery: 'Diagnose and unstick',
  integrate: 'Integrate phase members', merge: 'Merge accepted work',
};
const POOLS = ['implementation', 'review', 'reasoning', 'dailyStarts'];
const POOL_WORDS = {
  implementation: 'implementation runs at once (repairs too)',
  review: 'review runs at once',
  reasoning: 'reasoning runs at once (0: diagnoses use implementation)',
  dailyStarts: 'launches a day',
};

// Task 182: an agreement still written in the reserved model sets slots
// aside for phases even while they are idle. The way out is one explicit,
// revision-guarded step a person takes here; reading never migrates.
function migrationNoticeHtml() {
  const r = orch().reading;
  const legacy = r.effective.legacyAllocations || {};
  const rows = Object.entries(legacy).map(([n, a]) =>
    `<li>Phase #${esc(n)} reserves ${esc(Object.entries(a).map(([k, v]) => `${v} ${k}`).join(', '))}</li>`).join('');
  return `<article class="o-panel"><h3>Capacity is reserved per phase ${badge('migration available', 'alarm')}</h3>` +
    `<p>This agreement sets slots aside for named phases even while they are idle, unadopted or delivered, so other work can wait on a full board that is not working. Shared capacity makes every pool one total used first come, first served.</p>` +
    (rows ? `<ul class="o-list">${rows}</ul>` : '') +
    `<p><a data-route href="${esc(orchHref('agreement'))}">Preview the migration in the operating agreement</a></p></article>`;
}

function migrationHtml() {
  const o = orch();
  const r = o.reading;
  const eff = r.effective;
  if (!eff.migrationNeeded) return '';
  const m = o.migration && o.migration.base === eff.revision ? o.migration : null;
  const phases = m && m.result && m.result.phases ? m.result.phases : [];
  const list = phases.length ? `<ul class="o-list">${phases.map(p =>
    `<li>Phase #${esc(p.phase)}: reserved ${esc(Object.entries(p.allocation || {}).map(([k, v]) => `${v} ${k}`).join(', ') || 'nothing')} → ` +
    `${p.limits ? `its own limits ${esc(Object.entries(p.limits).map(([k, v]) => `${v} ${k}`).join(', '))} (reserve nothing)` : 'the default phase ceiling (reserves nothing)'}</li>`).join('')}</ul>` : '';
  return `<article class="o-panel"><h3>Migrate to shared capacity</h3>` +
    `<p>Keeps the global totals, every authority, pause, exclusion, hand-back, adoption, strategy, per-card ceiling, operation and today’s launch counts exactly as they are. Removes each phase’s reservation. Nothing is started, moved or adopted.</p>` +
    `<label class="o-setting" for="ag-keeplimits"><span>Keep each phase’s old numbers as its own limit (a ceiling that reserves nothing) instead of the default ceiling</span>` +
    `<input id="ag-keeplimits" type="checkbox" data-ofield="keeplimits" data-o="ag-keeplimits"${o.keepLimits ? ' checked' : ''}></label>` +
    `<div class="o-steps"><div class="o-action">${btn('migrate-preview', 'Preview the migration', { act: 'migrate', data: { apply: '' } })}<p class="o-effect">Shows the old and new limits; writes nothing.</p></div>` +
    `<div class="o-action">${btn('migrate-apply', 'Migrate to shared capacity', { act: 'migrate', data: { apply: '1' }, primary: true, disabled: !m, why: m ? '' : 'preview it first' })}` +
    `<p class="o-effect">Writes the agreement once, bound to revision ${esc(eff.revision)}; refused if it changed since.</p></div></div>` +
    (m ? `<section class="o-preview" role="status"><h4>Migration preview — nothing was written</h4>${list}` +
      ((m.result.queue || []).length ? `<p>What it changes for the queue now:</p><ul class="o-list">${m.result.queue.map(q => `<li>${esc(q.summary)}</li>`).join('')}</ul>` : '') +
      `${problemsHtml(m.result.problems)}` +
      `<p class="o-dim">Default phase ceiling after migrating: ${esc(Object.entries((m.result.after || {}).scopeDefaults || {}).map(([k, v]) => `${v} ${k}`).join(', '))}.</p></section>` : '') +
    `</article>`;
}
const CEILINGS = ['launchesPerCard', 'repairRounds', 'transientRetries', 'runAgeMinutes', 'recoveryAttempts', 'setupRetries'];

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
    (b.capacityModel === 'shared'
      ? `<h4>Global limits — shared, first come first served</h4>` +
        `<p class="o-dim">The most runs at once on the whole board, per pool, and launches a day. Standalone and phase work draw on the same totals, and only while a run holds a slot.</p>` +
        POOLS.map(p => field(`bench.capacity.${p}`, 'int', cap[p], POOL_WORDS[p])).join('') +
        `<h4>Default phase ceiling — reserves nothing</h4>` +
        `<p class="o-dim">Every phase scope inherits these as the most it may use at once (and launch in a day). A ceiling is a limit, not a share: an idle phase uses nothing. Give one phase its own limits below only where it needs them.</p>` +
        POOLS.map(p => field(`bench.scopeDefaults.${p}`, 'int', (b.scopeDefaults || {})[p], POOL_WORDS[p])).join('')
      : `<h4>Capacity — one shared total, allocated to phases (reserved model)</h4>` + POOLS.map(p => field(`bench.capacity.${p}`, 'int', cap[p], p)).join('')) +
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
  const shared = (doc.bench || {}).capacityModel === 'shared';
  return `<fieldset class="o-form"><legend>Phase #${esc(n)} — may only narrow</legend>` +
    field(`phases.${n}.enabled`, 'bool', p.enabled, 'Enabled') +
    field(`phases.${n}.paused`, 'bool', p.paused, 'Paused') +
    field(`phases.${n}.strategy`, 'strategy', p.strategy || 'sequential', 'Strategy') +
    `<h4>Authority (inherits the parent's; unchecking narrows)</h4>` +
    Object.keys(AUTHORITY_WORDS).map(w => field(`phases.${n}.authority.${w}`, 'pauth', parent[w] && auth[w] !== false,
      `${AUTHORITY_WORDS[w]}${parent[w] ? '' : ' — the parent does not grant it'}`, parent[w] ? '' : ' disabled')).join('') +
    (shared
      ? `<h4>Limits (blank inherits the default ceiling; reserves nothing)</h4>` +
        POOLS.map(k => field(`phases.${n}.limits.${k}`, 'intopt', (p.limits || {})[k], POOL_WORDS[k])).join('')
      : `<h4>Allocation from the Bench total (reserved model)</h4>` + POOLS.map(k => field(`phases.${n}.allocation.${k}`, 'int', alloc[k] || 0, k)).join('')) +
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
  parts.push(migrationHtml());
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
      `<fieldset class="o-form"><legend>Narrow one phase (optional)</legend><label class="o-setting" for="ag-newphase"><span>Phase card number</span>` +
      `<input id="ag-newphase" type="text" inputmode="numeric" data-ofield="newphase" data-o="ag-newphase" value="${esc(o.newPhase || '')}"></label>` +
      `<div class="o-actions">${btn('addphase', 'Add to the draft', { act: 'addphase' })}</div>` +
      `<p class="o-dim">Not needed to run a phase: every phase inherits the Bench scope and is queued by moving its card into To Do. An entry here only narrows one phase — its authority, ceilings or strategy.</p></fieldset>` +
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
    case 'trace-apply': {
      const t = traceState();
      t.filters = { ...t.draft };
      loadTrace(true);
      paintNow();
      return;
    }
    case 'trace-clear': {
      const t = traceState();
      t.filters = { ...NO_FILTERS };
      t.draft = { ...NO_FILTERS };
      loadTrace(true);
      paintNow();
      return;
    }
    case 'trace-reload': loadTrace(true); return;
    case 'trace-older': loadTrace(false, true); return;
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
        return res.ok ? { said: `phase ${d.action}d — nothing was launched; the next pass schedules it` } : res;
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
    case 'askConv': {
      // an earlier conversation opens in the shared panel (task 205): a
      // read. Its target's slot keeps any question typed there
      const c = (askState().list || []).find(x => x.id === d.conv);
      if (!c) return;
      openAgent('orch', slotTargetOf(c), { from: el, data: { conv: c.id, chosen: true }, focus: 'composer' });
      loadThread(c.id, true);
      return;
    }
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
    case 'migrate': {
      const body = { expected, keepLimits: !!o.keepLimits, apply: !!d.apply };
      if (!d.apply) {
        const res = await send('/api/orchestration/agreement/migrate', body, true);
        if (!res.ok) { o.notice = { tone: 'alarm', text: res.error }; paintNow(); return; }
        o.migration = { base: expected, keep: !!o.keepLimits, result: res.data.result };
        paintNow();
        return;
      }
      return armed(key, async () => {
        const res = await send('/api/orchestration/agreement/migrate', body, true);
        if (res.ok) { o.migration = null; return { said: `agreement ${res.data.result.revision} shares capacity — nothing was started, moved or adopted` }; }
        return res;
      });
    }
    case 'revert': o.edit = null; o.preview = null; paintNow(); return;
    case 'addphase': {
      const n = String(parseInt(o.newPhase, 10));
      if (!o.edit || n === 'NaN' || +n <= 0) { o.notice = { tone: 'alarm', text: 'a phase is named by its card number' }; paintNow(); return; }
      o.edit.doc.phases = o.edit.doc.phases || {};
      if (!o.edit.doc.phases[n]) {
        // shared capacity: a new phase inherits the default ceiling and
        // reserves nothing (task 182); only the reserved model allocates
        const shared = (o.edit.doc.bench || {}).capacityModel === 'shared';
        o.edit.doc.phases[n] = shared
          ? { enabled: true, paused: false, strategy: 'sequential', ceilings: {} }
          : { enabled: true, paused: false, strategy: 'sequential', allocation: {}, ceilings: {} };
      }
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

/* a control a person types or chooses in: its repaint waits for them */
function isField(a) {
  if (!a || !a.tagName) return false;
  if (a.tagName === 'TEXTAREA' || a.tagName === 'SELECT') return true;
  return a.tagName === 'INPUT' && !['checkbox', 'radio', 'button'].includes(a.type);
}

function editing(root) {
  const a = document.activeElement;
  return !!a && root.contains(a) && isField(a);
}

function paint(root, html) {
  const a = document.activeElement;
  const mark = a && root.contains(a) ? a.getAttribute('data-o') : null;
  if (!setHtml(root, html)) return;
  // an armed button drawn afresh keeps its countdown where its window is,
  // rather than starting the drain again from full
  for (const b of root.querySelectorAll('button.armed[data-o]')) {
    const left = arms.remaining(b.getAttribute('data-o'));
    if (left) b.style.setProperty('--arm-delay', `${Math.min(0, left - ARM_MS)}ms`);
  }
  if (mark) {
    const el = root.querySelector(`[data-o="${CSS.escape(mark)}"]`);
    if (el && typeof el.focus === 'function') el.focus({ preventScroll: true });
  }
}

function paintNow() {
  const root = $('#oshell');
  if (root && S.view === 'orchestrator') paint(root, shellHtml());
  // an armed button in the shared panel is drawn there (task 205)
  renderPanel();
}

function wire(root) {
  const o = orch();
  if (o.wired === root) return;
  o.wired = root;
  root.addEventListener('click', (e) => {
    // a fold's summary: remembered now, because `toggle` is dispatched
    // later and a repaint in between would take the fold it names
    const sum = e.target.closest && e.target.closest('summary');
    const fold = sum && sum.parentElement;
    if (fold && fold.dataset && fold.dataset.okeep) o.open[fold.dataset.okeep] = !fold.open;
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
    } else if (f === 'tf') {
      // a filter is a draft until Apply: typing reads nothing
      traceState().draft[el.dataset.k] = el.value;
    } else if (f === 'newphase') {
      o.newPhase = el.value;
    } else if (f === 'keeplimits') {
      o.keepLimits = !!el.checked;
      o.migration = null;           // a new choice needs a new preview
      if (e.type === 'change') paintNow();
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
    // a trace entry opened: its body is drawn, and its links read, now
    if (k && k.startsWith('tr-') && e.target.open) scheduleRender();
  }, true);
  // a field left: the repaint it held may run now. Only a field holds
  // one, so focus moving between links and buttons asks for nothing
  root.addEventListener('focusout', (e) => { if (isField(e.target)) setTimeout(scheduleRender, 0); });
  // a press holds the repaint until its click is dispatched (lib/press.js):
  // a repaint between the button going down and coming up would take the
  // pressed control out of the page, and the click with it
  root.addEventListener('pointerdown', (e) => { if (e.isPrimary !== false) pressing().down(); });
  if (!pressWired) {
    pressWired = true;
    // capture, so every click handler runs after the hold has lifted
    document.addEventListener('click', () => pressing().done(), true);
    document.addEventListener('pointerup', () => pressing().up(), true);
    document.addEventListener('pointercancel', () => pressing().done(), true);
  }
}

let press = null;
let pressWired = false;
function pressing() {
  if (!press) press = pressHold({ onRelease: scheduleRender });
  return press;
}

/* The view's paint: reads what is stale, holds while a person types, and
   never writes. */
function renderOrchestrator() {
  const root = $('#oshell');
  if (!root || !S.state) return;
  wire(root);
  loadOrch(false);
  // held while a person types, and while a pointer is down on the view:
  // the release asks for this paint again
  if (editing(root) || pressing().held()) return;
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
  closedQuestionHtml, loadOrch, openAskAddress, referencesIn, registerOrchAgent, renderOrchestrator, renderOrchStrip,
  renderOrchTab, setPath
};
