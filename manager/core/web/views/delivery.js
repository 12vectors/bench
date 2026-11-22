/* One delivery status per card. */
import { agentOnTask, loadState } from '../app/data.js';
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { DEFAULT_RETRY_MS, resource } from '../lib/resource.js';
import { fmtClock, fmtShort } from '../lib/time.js';
import { toast } from '../lib/toast.js';
import { actLabel, closeSheet, completeSheet, fireAgent, holdPhase, move, openPR, requiredSheet, runPhase, showDetail, verifySheet, wireAction } from './card.js';
import { setView } from './frame.js';
import { gateSheet } from './gates.js';
import { fold, stopAgent } from './sessions.js';
import { mix } from '../words.js';

/* ── one delivery status per card (task 99, design #94 §3, §3.6) ───────

   What is happening, what to do next, and what prevents delivery are
   answered once, by the server (`delivery.py`), and carried in
   `S.state.delivery`. The card face, the Overview and the merge dialog
   draw that reading and nothing else: there is no status cascade on this
   page. Mutations never read it either — every action below calls the
   route that rechecks its own rules at the moment it acts. */

// The face wears a status only when it changes what you do next (§3.6):
// active work, an actionable current problem, an override in force, a
// publication problem, and — in review/ — the next delivery step. The
// rest (not started, queued, ready to start, in progress, an ungated
// review, delivered) is the column's own word and stays off the face.
const FACE_WORDS = new Set([
  'merging', 'not-published', 'run-failed', 'agent-working', 'phase-running',
  'returned', 'cannot-assess', 'rules-invalid', 'check-failed', 'cannot-run-check',
  'changes-requested', 'checks-running', 'checks-needed', 'review-needed',
  'signoff-needed', 'ready-to-merge', 'ready-overridden', 'merged-elsewhere',
  'waiting-for-phase', 'waiting-for-parent',
  // task 189: met at a head that does not hold the current target
  'target-changed', 'target-conflicts',
]);
// the words whose cause is a requirement on the current commit, so the ◫
// evidence earns the face beside them; a stale or historical row never does
const EVIDENCE_ON_FACE = new Set(['check-failed', 'cannot-run-check', 'checks-running']);

function deliveryOf(task) {
  return ((S.state || {}).delivery || {})[task.file] || null;
}

function toneColours(tone) {
  switch (tone) {
    case 'working': return { tint: 'var(--accent)', bg: mix('var(--accent)', 16) };
    case 'alarm': return { tint: 'var(--alarm)', bg: mix('var(--alarm)', 16) };
    case 'calm': return { tint: 'var(--calm)', bg: mix('var(--calm)', 18) };
    // a step that lies ahead and is not a failure (task 189)
    case 'attention': return { tint: 'var(--attention)', bg: mix('var(--attention)', 16) };
    case 'idle': return { tint: 'var(--idle)', bg: 'var(--sunken)' };
    default: return { tint: 'var(--muted)', bg: 'var(--sunken)' };
  }
}

function deliveryPill(d) {
  const c = toneColours(d.status.tone);
  return { text: d.status.label, tint: c.tint, bg: c.bg, title: d.status.sentence,
           word: d.status.word, tone: d.status.tone };
}

function shortSha(sha) { return sha ? String(sha).slice(0, 12) : ''; }

/* The headline count: required obligations only (design §7). Optional
   checks and earlier runs are counted apart and never inflate it. */
function requirementsHeadline(d) {
  const c = d.counts || { required: 0, met: 0 };
  const at = (d.candidate || {}).state === 'present' && d.candidate.commit
    ? ` for ${shortSha(d.candidate.commit)}` : '';
  if (!c.required) return 'No merge requirements apply';
  return `${c.met} of ${c.required} merge requirement${c.required === 1 ? '' : 's'} met${at}`;
}

const REQ_GLYPH = { met: '✓', needed: '○', running: '◌', problem: '✕', 'cannot-assess': '!' };
const REQ_ROW = { met: 'pass', needed: '', running: 'accent', problem: 'fail', 'cannot-assess': 'fail' };

function requirementRows(rows) {
  if (!rows || !rows.length) return '';
  return rows.map(r => {
    const overridden = r.state === 'overridden';
    const cls = overridden ? 'fail' : (REQ_ROW[r.class] || '');
    const glyph = overridden ? '!' : (REQ_GLYPH[r.class] || '·');
    const name = r.kind === 'check' ? `${r.name} — ${r.coverage}` : r.name;
    // A record of a declared equivalent (card 104) keeps its own name,
    // evidence and requester, and is said to be that; one that is declared
    // and is not the same check says which dimension stops it.
    const via = r.reusedFrom
      ? `<span class="dreqvia">run as <code>${esc(r.reusedFrom.check)}</code>` +
        ` (evidence ${esc(r.reusedFrom.evidence || '')}, run for ` +
        `${esc(r.reusedFrom.agent || r.reusedFrom.actor || 'an earlier request')}` +
        `), which this check accepts as the same check</span>`
      : r.equivalence
        ? `<span class="dreqvia"><code>${esc(r.equivalence.check)}</code> ran ` +
          `here and does not count: ${esc(r.equivalence.why || r.equivalence.dimension)}</span>`
        : '';
    return `<li class="check-row ${cls}" title="${esc(r.why || '')}">` +
      `<span class="glyph" aria-hidden="true">${glyph}</span>` +
      `<span class="name">${esc(name)}</span>` +
      `<span class="state">${esc(overridden ? 'Overridden — still unmet' : (r.plain || r.state))}` +
      `${r.remedy && ['problem', 'cannot-assess'].includes(r.class)
        ? `<span class="dreqfix">${esc(r.remedy)}</span>` : ''}${via}</span></li>`;
  }).join('');
}

/* An action from the reading, as a button wired to the route that does it
   today. `kind: view` opens something on this page and changes nothing; a
   disabled action is drawn disabled with its reason, never as a button
   that does nothing. */
function deliveryRun(task, a) {
  const post = async (url, body) => {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                   body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, data };
  };
  switch (a.action) {
    case 'move': return () => move(task.file, task.stage, a.to);
    case 'start': return () => fireAgent(task, '/api/agent/start');
    case 'takeover': return () => fireAgent(task, '/api/agent/start', { takeover: true });
    case 'hold': return () => stopAgent(a.agent || (agentOnTask(task.file) || {}).id);
    case 'run-phase': return () => runPhase(task, task.assignee && S.state.me
      && task.assignee !== S.state.me ? { takeover: true } : undefined);
    case 'hold-phase': return () => holdPhase(task);
    case 'act-pr': return () => fireAgent(task, '/api/agent/act-pr');
    case 'review-pr': return () => fireAgent(task, '/api/agent/review-pr');
    case 'open-pr': return () => openPR(task);
    // the completion sheet is the confirmation: it reads what merge &
    // clean up would do before offering it, exactly as dragging to done/
    case 'complete': return () => { completeSheet(task, task.stage); return true; };
    // task 189: armed; bound to the head the card showed. A clean update
    // pushes the card's own branch; a conflict starts one repair run.
    case 'update-target': return async () => {
      const d = deliveryOf(task) || {};
      const { ok, data } = await post('/api/card/integration/update', {
        file: task.file, head: (d.candidate || {}).commit || null });
      const r = data.result || {};
      toast(!ok ? (data.error || 'nothing was updated')
        : r.sentence || 'updated against its target', !ok || r.outcome === 'conflict');
      await loadState();
      return ok;
    };
    case 'cancel-check': return async () => {
      const { ok, data } = await post('/api/verify/cancel', { id: a.evidence });
      toast(ok ? 'the check was cancelled — nothing else changed' : (data.error || 'not cancelled'), !ok);
      await loadState();
      return ok;
    };
    case 'check-remote': return async () => {
      const { ok, data } = await post('/api/card/delivery/check-remote', { file: task.file });
      const f = data.found || {};
      toast(!ok ? (data.error || 'the remote was not asked')
        : f.error ? f.error
        : Object.keys(f.found || {}).length ? `fetched ${Object.keys(f.found).join(', ')} from ${f.remote}`
        : `${f.remote || 'the remote'} has neither branch for this card`, !ok || !!f.error);
      await loadState();
      return ok;
    };
    case 'launch-check': return async () => {
      const { ok, data } = await post('/api/card/delivery/launch-check',
        // the intent and the ownership rule are the server's, carried on
        // the action: the check asks about the launch this card offers
        { file: task.file, stage: task.stage, intent: a.intent, takeover: !!a.takeover });
      toast(ok ? `${(data.launch || {}).says || 'asked'} — nothing was claimed or started`
               : (data.error || 'the launch check could not be asked'),
            !ok || !(data.launch || {}).ok);
      return ok;
    };
    case 'read-failure': return () => {
      const d = deliveryOf(task);
      if (d && d.status.word === 'run-failed') {
        showDetail(task);
        requestAnimationFrame(() => { const f = $('#dfail'); if (f) { f.scrollIntoView({ block: 'nearest' }); f.focus(); } });
      } else verifySheet(task);
      return true;
    };
    // the sheet is the confirmation: it reads what a click would request
    // and holds the armed fire, as the completion sheet does for a merge
    case 'run-required-checks': return () => { requiredSheet(task); return true; };
    case 'evidence': return () => { verifySheet(task); return true; };
    // a merge rule asks for sign-off: open the Acceptance section, where
    // Track acceptance and each criterion's decision already live. Opening
    // it changes nothing; every decision is still its own armed write
    case 'confirm-criteria': return () => {
      S.dtab[task.file] = 'acceptance';
      closeSheet();
      showDetail(task);
      return true;
    };
    case 'gates': return () => { gateSheet(task); return true; };
    case 'phases': return () => { setView('phases'); return true; };
    case 'settings-merge-rules': return () => {
      S.settingsSection = 'acceptance';
      closeSheet();
      setView('settings');
      return true;
    };
    default: return null;
  }
}

/* One action as markup, and its wiring after it lands. The key is the
   card's and the action's, so the primary on the Overview and the same
   action in the dialog are one armed window, not two. */
function deliveryButtonHtml(a, slot, primary) {
  if (!a) return '';
  const why = !a.enabled && a.why
    ? `<small class="dwhy" id="dwhy-${slot}">${esc(a.why)}</small>` : '';
  if (a.action === 'open-pr-url' && a.url) {
    return `<a class="dact${primary ? ' dprimary' : ''}" href="${esc(a.url)}" target="_blank" ` +
      `rel="noopener" data-dslot="${slot}">${esc(a.label)} ↗</a>`;
  }
  // an action that names what it acts on (task 174: a phase's repair
  // names the integration branch) says so beside itself, before the
  // confirmation, rather than only in a tooltip
  const target = a.target && a.enabled
    ? `<small class="dtarget" id="dtarget-${slot}">on <code>${esc(a.target)}</code></small>` : '';
  const title = a.title ? ` title="${esc(a.title)}"` : '';
  const described = a.enabled ? (target ? ` aria-describedby="dtarget-${slot}"` : '')
                              : ` disabled aria-describedby="dwhy-${slot}"`;
  return `<span class="dactwrap"><button type="button" class="dact${primary ? ' dprimary' : ''}" ` +
    `data-dslot="${slot}"${title}${described}>` +
    `${a.arm && a.enabled ? actLabel(a.label, ACT_CONFIRM[a.action] || 'click again to confirm', 'working…')
                          : esc(a.label)}</button>${target}${why}</span>`;
}

const ACT_CONFIRM = {
  start: 'start it?', takeover: 'take it over?', hold: 'hold it?', 'run-phase': 'run it?',
  'hold-phase': 'hold it?', 'act-pr': 'act on it?', 'review-pr': 'review it?',
  'cancel-check': 'cancel it?', 'update-target': 'update it?',
};

function wireDeliveryButtons(root, task, actions) {
  root.querySelectorAll('button[data-dslot]').forEach(btn => {
    const a = actions[btn.dataset.dslot];
    if (!a || !a.enabled) return;
    const run = deliveryRun(task, a);
    if (!run) return;
    if (a.arm) {
      wireAction(btn, `${task.file}::delivery::${a.action}`, {
        label: a.label, confirm: ACT_CONFIRM[a.action] || 'click again to confirm',
        busy: 'working…', run });
    } else {
      btn.addEventListener('click', (e) => { e.stopPropagation(); run(); });
    }
  });
}

/* The status block every surface draws: the word as a heading, the
   sentence, the primary action first, then the alternatives in one native
   disclosure. An alarm is announced as one; every other tone is a status. */
function deliveryStatusHtml(task, d, where) {
  if (!d) return `<p class="dnote">Reading the delivery status…</p>`;
  const c = toneColours(d.status.tone);
  const others = (d.others || []);
  const id = `dstatus-${where}`;
  const who = d.primary ? ({ you: 'you', agent: 'an agent', board: 'the board',
                             nobody: 'nobody' }[d.primary.who] || d.primary.who) : '';
  return `<section class="dstatus tone-${esc(d.status.tone)}" aria-labelledby="${id}" ` +
    `data-word="${esc(d.status.word)}">` +
    `<h3 id="${id}" class="dsword" style="color:${c.tint}">${esc(d.status.label)}</h3>` +
    `<p class="dssent" role="${d.status.tone === 'alarm' ? 'alert' : 'status'}">${esc(d.status.sentence)}</p>` +
    `<div class="dsacts">` +
    (d.primary ? deliveryButtonHtml(d.primary, `${where}-p`, true) +
                 (who ? `<span class="dswho">next · ${esc(who)}</span>` : '')
               : `<span class="dswho">nothing is yours to do here right now</span>`) +
    `</div>` +
    (others.length
      ? `<details class="dsmore" data-fold="${where}-more"${S.accOpen[`${task.file}:${where}-more`] ? ' open' : ''}>` +
        `<summary>More actions (${others.length})</summary><div class="dsmorebody">` +
        others.map((a, i) => deliveryButtonHtml(a, `${where}-o${i}`, false)).join('') +
        `</div></details>`
      : '') +
    `</section>`;
}

function deliveryActions(d, where) {
  const out = {};
  if (!d) return out;
  if (d.primary) out[`${where}-p`] = d.primary;
  (d.others || []).forEach((a, i) => { out[`${where}-o${i}`] = a; });
  return out;
}

/* The full reading — the requirement rows, their remedies, the candidate
   and where each fact came from. A GET that writes nothing; refetched
   whenever the server says the rows it would return have changed.

   The key carries the server's own `detail` — a digest of every row, its
   prose and remedy, the merge rule's reading and the completion — so a
   check replaced by another with the same totals, or a reworded remedy,
   is a new key even when the word, the counts and the classes are not.
   The race policy is lib/resource.js's (task 125): one read in flight and
   one follow-up queued, the newest reading standing, and a failed read
   keeping the last rows, marked with the error and `stale`, asked again
   after a short wait rather than on every repaint. */
const DFULL_RETRY_MS = DEFAULT_RETRY_MS;

function deliveryFullKey(task) {
  const d = deliveryOf(task);
  return JSON.stringify([task.stage, task.mtime, d && d.detail, d && d.status, d && d.counts,
                         d && d.candidate, d && d.classes]);
}

const deliveryFull = resource({
  store: S.dfull,
  url: (file, task) => `/api/card/delivery?file=${encodeURIComponent(file)}` +
    `&stage=${encodeURIComponent(task.stage)}`,
  accept: (ok, body) => ok && body && body.delivery
    ? { data: body.delivery }
    : { error: (body && body.error) || 'the board did not answer' },
  ask: (url) => fetch(url),
});

/* A reading another surface has just made (the merge dialog reads the
   rows fresh): it stands as the newest. */
function storeDeliveryFull(file, key, data) { deliveryFull.put(file, key, data); }

function loadDeliveryFull(task, done) {
  const entry = deliveryFull.read(task.file, deliveryFullKey(task), { arg: task, done });
  return (entry && entry.data) || null;
}

/* Merge requirements, as supporting detail under the status: the required
   rows with their state in words, then — folded — the optional checks and
   earlier runs that are counted apart, and the technical provenance. */
function requirementsHtml(task, d, full, where) {
  if (!d) return '';
  const rows = full ? full.requirements : null;
  const hist = d.history || {};
  const gated = (S.state.gates || {})[task.file];
  const fold = (key, summary, body) =>
    `<details class="fold dreqfold" data-fold="${where}-${key}"` +
    `${S.accOpen[`${task.file}:${where}-${key}`] ? ' open' : ''}><summary>${summary}</summary>` +
    `<div class="dfoldbody">${body}</div></details>`;
  const optional = (hist.optional || 0) + (hist.older || 0) + (hist.obsolete || 0);
  const tech = full ? [
    ['Commit', full.candidate.state === 'present'
      ? `${shortSha(full.candidate.commit)} on ${full.candidate.ref || 'its branch'}`
      : `${full.candidate.state}${full.candidate.why ? ' — ' + full.candidate.why : ''}`],
    ['Merge rule', full.decision ? `${full.decision.scope}: ${full.decision.decision} — ${full.decision.summary || ''}`
                                 : 'no merge rule was read for this stage'],
    ['Completion', full.completion ? `${full.completion.mode} merge into ${full.completion.target}` +
      (full.completion.refusal ? ` — ${full.completion.refusal}` : '') : ''],
    ['Pull request', full.pr ? `${full.pr.prState || 'state unknown'}${full.pr.merged ? ', merged' : ''}` +
      (full.source && full.source.pr && full.source.pr.observed
        ? ` · as of ${fmtShort(full.source.pr.observed)} (${full.source.pr.from || 'snapshot'})` : '')
      : 'not observed'],
    ['Read', `${fmtClock(full.read.at)} · local to this board · reading it ran nothing`],
  ].filter(([, v]) => v) : [];
  const failed = (S.dfull[task.file] || {}).error;
  return integrationHtml(task, d, where) +
    `<section class="dreqs" aria-labelledby="dreqs-${where}">` +
    `<h3 id="dreqs-${where}">Merge requirements · ${esc(requirementsHeadline(d))}</h3>` +
    (failed
      ? `<p class="dnote dreqerr" role="status">The requirement rows could not be read: ${esc(failed)}. ` +
        `${rows ? 'The rows below are the last reading, and may be out of date' : 'Nothing is shown in their place'}` +
        ` — they are asked again in a few seconds.</p>`
      : '') +
    (!gated && !(d.counts || {}).required
      ? `<p class="dnote">No merge requirements are configured on this board. Nothing is required before a merge, and that is not a pass.</p>`
      : rows === null ? `<p class="dnote">Reading the requirements…</p>`
      : rows.length ? `<ul class="dreqlist">${requirementRows(rows)}</ul>`
      : (hist.obsolete
          ? `<p class="dnote">The pull request is already merged, so no requirement applies to closing it.</p>`
          : '')) +
    (optional
      ? fold('optional', `Optional checks and earlier runs (${optional})`,
          `<p class="dnote">Counted apart from the merge requirements, and never a reason a merge waits.</p>` +
          `<ul class="dhist">` +
          (hist.optional ? `<li>${hist.optional} optional check${hist.optional === 1 ? '' : 's'} — registered or run, required by no merge rule</li>` : '') +
          (hist.older ? `<li>${hist.older} earlier run${hist.older === 1 ? '' : 's'} on other commits — history, not today's reason</li>` : '') +
          (hist.obsolete ? `<li>${hist.obsolete} requirement${hist.obsolete === 1 ? '' : 's'} the merged pull request made obsolete</li>` : '') +
          `</ul><button type="button" class="dlink" data-dview="evidence">Open ◫ evidence</button>`)
      : '') +
    (tech.length
      ? fold('technical', 'Technical details',
          `<dl class="dfacts">` + tech.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('') + `</dl>`)
      : '') +
    `</section>`;
}

/* The candidate against the target it lands in (task 189): the server's
   reading, apart from the review — which target, which of its revisions,
   what the head holds, what an update would bring in, and what the
   provider's mergeability is (and is not) evidence of. Draws, decides
   nothing. */
const INTEGRATION_GLYPH = {
  'ready-to-admit': '✓', 'up-to-date': '·', 'integration-check-needed': '○',
  'integration-check-running': '◌', 'integration-check-failed': '✕',
  'target-changed': '↺', conflicts: '✕', updating: '◌', unknown: '?',
  'no-candidate': '·',
};

function integrationHtml(task, d, where) {
  const g = d && d.integration;
  if (!g || !g.state) return '';
  const c = toneColours(g.tone);
  const id = `dint-${where}`;
  const facts = [
    ['Target', `${g.target}${g.targetTip ? ` at ${shortSha(g.targetTip)}` : ''}` +
      (g.authority === 'remote-tracking' ? ` (${g.targetRef}, as last fetched)`
        : g.authority === 'local' ? ' (this checkout)' : '')],
    ['Candidate', g.head ? shortSha(g.head) : 'none'],
    ['Holds the target', g.fresh === true ? 'yes' : g.fresh === false
      ? `no${g.behind != null ? ` — ${g.behind} commit${g.behind === 1 ? '' : 's'} behind` : ''}` : 'not known'],
    ['Required for admission', g.required
      ? 'yes — the merge rule requires a check, so it must run on the combined head'
      : 'no — no merge rule requires a check here; shown for information'],
    ['Provider mergeability', !g.provider || g.provider.conflicts == null ? 'not reported'
      : (g.provider.conflicts ? 'conflicts' : 'no textual conflict') +
        ` (about ${g.provider.about}) — a textual reading, never evidence the combination works`],
    ['Last update', g.attempt ? `${g.attempt.outcome}${g.attempt.conflicts && g.attempt.conflicts.length
      ? ` on ${g.attempt.conflicts.join(', ')}` : ''} · ${g.attempt.at || ''}` : 'none on this board'],
  ];
  const incoming = (g.incoming || []).length
    ? `<details class="fold dreqfold" data-fold="${where}-incoming"` +
      `${S.accOpen[`${task.file}:${where}-incoming`] ? ' open' : ''}>` +
      `<summary>Accepted target commits it does not hold (${g.incoming.length}${g.behind > g.incoming.length ? ` of ${g.behind}` : ''})</summary>` +
      `<div class="dfoldbody"><ul class="dhist">` +
      g.incoming.map(i => `<li><code>${esc(shortSha(i.commit))}</code> ${esc(i.subject || '')}</li>`).join('') +
      `</ul></div></details>` : '';
  return `<section class="dint tone-${esc(g.tone || 'neutral')}" aria-labelledby="${id}" ` +
    `data-integration="${esc(g.state)}">` +
    `<h3 id="${id}">Against the target · <span style="color:${c.tint}">` +
    `<span aria-hidden="true">${INTEGRATION_GLYPH[g.state] || '·'}</span> ${esc(g.label)}</span></h3>` +
    `<p class="dnote">${esc(g.sentence || '')}</p>` +
    `<dl class="dfacts">` + facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('') + `</dl>` +
    incoming +
    `<details class="fold dreqfold" data-fold="${where}-guarantee"` +
    `${S.accOpen[`${task.file}:${where}-guarantee`] ? ' open' : ''}><summary>Review is not admission</summary>` +
    `<div class="dfoldbody"><p class="dnote">A review judges this card against its brief. Admission ` +
    `is the merge into ${esc(g.target)} as it is now: where a merge rule requires a check, the check ` +
    `must have run on a head that holds the target's accepted tip. Bench never merges another card's ` +
    `pending work in to find out. ${esc(g.guarantees || '')}.</p></div></details>` +
    `</section>`;
}

export {
  ACT_CONFIRM, deliveryActions, deliveryButtonHtml, deliveryFullKey,
  deliveryFull, deliveryOf, deliveryPill, deliveryRun, deliveryStatusHtml,
  DFULL_RETRY_MS, EVIDENCE_ON_FACE, FACE_WORDS, integrationHtml, loadDeliveryFull,
  REQ_GLYPH, REQ_ROW, requirementRows, requirementsHeadline,
  requirementsHtml, shortSha, storeDeliveryFull, toneColours,
  wireDeliveryButtons
};
