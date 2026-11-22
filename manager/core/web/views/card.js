/* The card face, its actions and sheets, and where a PR review ran. */
import { agentOnTask, allTasks, failedRun, loadState, modelChip, scheduleRender, sessionMeta, whyFailed } from '../app/data.js';
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { ARM_MS, arming, BUSY_MS, phaseOf } from '../lib/arm.js';
import { $, esc } from '../lib/dom.js';
import { scrollMarks } from '../lib/keep.js';
import { keyedNode } from '../lib/keyed.js';
import { plainClick, syncRoute } from '../lib/nav.js';
import { cardId, formatRoute } from '../lib/route.js';
import { ago, elapsed } from '../lib/time.js';
import { toast } from '../lib/toast.js';
import { archiveCard, renderBar } from './activity.js';
import { phaseFlight, phaseFullName, phaseSummary, planChipHtml, planChipLabel, planChipTitle, planSheet, stackChipTitle } from './board.js';
import { FACE_WORDS, deliveryOf, deliveryPill, shortSha } from './delivery.js';
import { renderDrawer } from './detail/drawer.js';
import { acceptanceChips } from './detail/sections.js';
import { findTask } from './focus.js';
import { renderCards, setView } from './frame.js';
import { gateChip, gateSheet } from './gates.js';
import { canonicalNumber, mergedIn } from './phases.js';
import { profileSheet, reviewIsIn } from './profile.js';
import { progressWell } from './progress.js';
import { stopAgent } from './sessions.js';
import { SETTINGS_HEADERS } from './settings/policy.js';
import { fmtSecs, verifyChip, verifyRows, verifyState } from './verification.js';
import { STAGE_TINT } from '../words.js';

/* ── where a PR review ran, and what it was of ─────────────────────────

   A review used to be a verdict with no location and no revision: it ran
   in the live application checkout, at whatever that happened to hold.
   Now it runs in a checkout bench pinned to the exact head the pull
   request proposed, and these two read that record back.

   The one thing this must never do is imply currency it does not have. A
   verdict is about the commit it was pinned to, so a head that has moved
   makes it `superseded` and a merged PR makes it `historical` — and
   neither is drawn in the colour of a pass. */

const REVIEW_LABELS = {
  current: ['', 'reviewed', 'this is the head the pull request still proposes'],
  superseded: ['bad', 'superseded',
               "the PR's head has moved since — this says nothing about the "
               + 'code there now'],
  historical: ['dim', 'historical',
               'the pull request is merged or closed — a record of what it '
               + 'proposed, not a validation of what is in its base now'],
  none: ['', 'reviewed', ''],
};

const REVIEW_TARGETS = {
  member: "one card's own pull request",
  integration: "a phase's integration branch",
};

function reviewChip(task) {
  const r = (S.state.reviews || {})[task.file];
  if (!r) return null;
  const [cls, word, why] = REVIEW_LABELS[r.label] || REVIEW_LABELS.none;
  const head = (r.head || '').slice(0, 12);
  return {
    label: `${word} ${head}`, pre: '⌾', review: true,
    // accent means an agent is alive in it; otherwise the state's own
    // colour, and never the colour of a pass — a pinned review is a
    // location and a revision, not a verdict
    cls: r.live ? 'accent' : cls,
    title: `A PR review ran in a checkout bench pinned for it, detached at `
         + `${r.head || 'an unrecorded commit'}`
         + (r.base ? `, which PR #${r.number} proposes into ${r.base}` : '')
         + `. ${why}. Opening this runs nothing.`,
  };
}

function reviewPanel(record, evidence, current) {
  const fresh = record.freshness || {};
  const word = (REVIEW_LABELS[record.label] || REVIEW_LABELS.none)[1];
  const head = (record.head || {}).commit || '';
  const base = record.base || {};
  const rows = (evidence || []).filter(e => e.agent === record.agent);
  const checks = rows.length
    ? rows.map(e => {
        const [c, glyph, said] = verifyState((e.outcome === 'passed'
          && (fresh.stale || fresh.historical)) ? 'stale' : e.outcome);
        // preparation apart from the check it prepared, as the evidence
        // sheet draws it: "the dependencies would not install" and "the
        // tests failed" are different things to go and do
        const prep = (e.prepare || []).map(p => {
          const ok = p.outcome === 'prepared';
          return `<div class="check-row ${ok ? 'pass' : 'fail'}" ` +
            `style="padding-left:18px"><span class="glyph">${ok ? '✓' : '✕'}</span>` +
            `<span class="name">prepare · ${esc(p.name)}</span>` +
            `<span class="state">${esc(ok ? 'prepared' : (p.outcome || 'failed'))}` +
            `</span></div>`;
        }).join('');
        return `<div class="check-row ${c}" title="${esc(e.why || '')}">` +
          `<span class="glyph">${glyph}</span>` +
          `<span class="name">${esc(e.check)}</span>` +
          `<span class="state">${esc(said)} · in the pinned checkout` +
          `${e.duration ? ' · ' + fmtSecs(e.duration) : ''}</span></div>` + prep;
      }).join('')
    : `<div class="check-row none"><span class="glyph">—</span>` +
      `<span class="name">no checks ran here</span>` +
      `<span class="state">this review executed nothing bench recorded` +
      `</span></div>`;
  const row = (glyph, name, value, kls) =>
    `<div class="check-row ${kls || ''}"><span class="glyph">${glyph}</span>` +
    `<span class="name">${esc(name)}</span>` +
    `<span class="state">${esc(value)}</span></div>`;
  const where = record.removed
    ? `${record.path} — removed when the review ended`
    : record.kept ? record.kept
    : record.released ? `${record.path} — ${record.released}`
    : `${record.path} — held by this run`;
  return `<div class="panel"><div class="phead">` +
    `<span class="label">${esc(word)}</span>` +
    `<span class="r">${esc(record.reviewId || record.agent)}</span></div>` +
    row('⌾', 'reviewed head', `${head || 'unknown'} (${(record.head || {}).authority || 'unknown'} authority)`) +
    row('→', 'proposed into', `${base.ref || 'unknown'}${base.commit ? ' at ' + base.commit.slice(0, 12) : ''}`) +
    row('·', 'pull request', `#${record.number} · ${String(record.state || 'state unknown').toLowerCase()} · ${(REVIEW_TARGETS[record.target] || record.target || '')}`) +
    row('·', 'checkout', where, record.kept ? 'fail' : '') +
    checks +
    ((fresh.changed || []).length
      ? (fresh.changed || []).map(c => row('✕', 'not current', c, 'fail')).join('')
      : row('✓', 'still current',
            `the pull request is still at ${(current && current.commit || head).slice(0, 12)}`,
            'pass')) +
    // a caveat is not a change: a head read from the local branch has not
    // been superseded by anything, it was never bound — said in the
    // settled register rather than the alarm
    (fresh.caveats || []).map(c => row('·', 'unbound', c)).join('') +
    (record.notes || []).map(n => row('·', 'note', n)).join('') +
    `</div>`;
}

/* The detail panel. A GET of records and of what the PR poll last saw:
   it provisions nothing, fetches nothing from GitHub and starts no
   validation — opening a historical review must not be a way to set
   anything running against an integration branch it never judged. */
async function reviewSheet(task) {
  const wrap = $('#sheetwrap');
  const head = (tail) =>
    `<div class="stitle"><span>${esc(task.title)}</span>` +
    `<span class="mono">${task.number ? '#' + esc(task.number) + ' · ' : ''}` +
    `⌾ ${esc(tail)}</span></div>`;
  wrap.innerHTML = `<div class="sheet">${head('pr review')}<p>Reading the record…</p></div>`;
  wrap.classList.add('open');
  wrap.addEventListener('click', (e) => { if (e.target === wrap) closeSheet(); });

  const res = await fetch('/api/review?file=' + encodeURIComponent(task.file) +
                          '&stage=' + encodeURIComponent(task.stage));
  const data = res.ok ? await res.json() : {};
  const records = data.reviews || [];
  const current = data.head || null;

  wrap.innerHTML =
    `<div class="sheet">${head('pr review')}` +
    `<p>A PR review does not run in the application checkout. Bench resolves ` +
    `the pull request's head, checks that one commit out into a directory it ` +
    `owns for the run, and removes it afterwards — so a review reads the ` +
    `proposed code at a revision that cannot change under it.</p>` +
    `<div class="panel"><div class="phead">` +
    `<span class="label">The pull request now</span>` +
    `<span class="r">${esc(current && current.source === 'poll' ? 'from the PR poll' : 'last read')}</span></div>` +
    `<div class="check-row"><span class="glyph">·</span>` +
    `<span class="name">head</span><span class="state">` +
    `${esc(current && current.commit ? current.commit : 'bench has not read it')}` +
    `${current && current.state ? ' · ' + esc(String(current.state).toLowerCase()) : ''}` +
    `</span></div></div>` +
    (records.length
      ? records.map(r => reviewPanel(r, data.evidence, current)).join('')
      : `<div class="panel"><div class="check-row none"><span class="glyph">—</span>` +
        `<span class="name">no review has been pinned</span>` +
        `<span class="state">◔ review PR pins a checkout of the head</span>` +
        `</div></div>`) +
    `<p class="why">Reading this ran nothing: no checkout was made, nothing ` +
    `was fetched and no check was started. A superseded or historical ` +
    `review stays exactly what it was — the judgement of one revision — ` +
    `and validating what is in a base branch now is that branch's own card, ` +
    `never this panel.</p>` +
    `<div class="sbtns"><button id="sh-norev">Close<small>Nothing was run.` +
    `</small></button></div></div>`;
  $('#sh-norev').addEventListener('click', closeSheet);
}

async function fetchVerifyLog(evidenceId, into) {
  into.innerHTML = '<pre>reading the log…</pre>';
  try {
    const res = await fetch('/api/evidence/log?id=' + encodeURIComponent(evidenceId));
    const data = await res.json();
    into.innerHTML = res.ok
      ? `<p class="why">Scrubbed of anything shaped like a credential on the ` +
        `way out${data.clipped ? ', and clipped to its tail' : ''}.</p>` +
        `<pre>${esc(data.text || '(the log is empty)')}</pre>`
      : `<pre>${esc(data.error || 'the log could not be read')}</pre>`;
  } catch {
    into.innerHTML = '<pre>the log could not be read</pre>';
  }
}

/* The detail panel. A GET of what is stored and nothing else: opening it
   re-runs nothing, which is the one thing a panel like this must not do.
   Rerunning is the armed ↻ chip per check, and it says what it costs. */
async function verifySheet(task) {
  const wrap = $('#sheetwrap');
  const head = (tail) =>
    `<div class="stitle"><span>${esc(task.title)}</span>` +
    `<span class="mono">${task.number ? '#' + esc(task.number) + ' · ' : ''}` +
    `◫ ${esc(tail)}</span></div>`;
  wrap.innerHTML = `<div class="sheet">${head('verification')}<p>Reading the evidence…</p></div>`;
  wrap.classList.add('open');
  wrap.addEventListener('click', (e) => { if (e.target === wrap) closeSheet(); });

  const res = await fetch('/api/evidence?file=' + encodeURIComponent(task.file) +
                          '&stage=' + encodeURIComponent(task.stage));
  const data = await res.json();
  const rollup = data.rollup || { rows: [] };
  const rows = rollup.rows || [];
  const names = [...new Set(rows.map(r => r.check))];

  wrap.innerHTML =
    `<div class="sheet">${head('verification')}` +
    `<p>Bench ran these itself. Each outcome below is the exit status of a ` +
    `process this board launched and waited on — not a report from a run, ` +
    `and not an agent's exit code. A check nothing asked for reads ` +
    `<b>not run</b>.</p>` +
    `<div class="panel"><div class="phead"><span class="label">Named checks</span>` +
    `<span class="r">${esc(rollup.location === 'local' ? 'local to this board' : String(rollup.location || ''))}</span></div>` +
    // with the verifier reports beside the rows (task 135), when any
    // record carries one
    verifyRows(rollup, { links: true, task, reports: data.evidence || [] }) +
    `</div>` +
    `<div class="panel"><div class="phead"><span class="label">Run one again</span>` +
    `<span class="r">a person's action</span></div>` +
    (names.length
      ? `<div class="chiprow">` + names.map(n =>
          `<button class="chip2" data-rerun="${esc(n)}">` +
          actLabel('↻ ' + n, 'run it?', 'running…') + `</button>`).join('') +
        `</div><p class="why">Side effects, in full: bench runs that check's ` +
        `command in this card's own checkout (or, for a <span class="mono">source</span> ` +
        `check, in a detached checkout it makes and removes), with its ` +
        `preparation steps first. It writes a new evidence record and a new ` +
        `log — always a new run, even where a current pass of the same ` +
        `check at this commit could have been reused; it moves no card, ` +
        `touches no branch and publishes nothing. ` +
        `Build output, caches and temporary files land in the directory bench ` +
        `points HOME and TMPDIR at. A run already in flight for the same ` +
        `check is joined rather than doubled. A check that outlasts this ` +
        `page's own patience is still running: the row above goes to ` +
        `<b>running now</b> and the outcome arrives when the runner has ` +
        `one — nothing is fired twice.</p>`
      : `<p class="why">Nothing to rerun — this card has no named checks.</p>`) +
    `</div>` +
    `<div id="evlog"></div>` +
    `<details class="fold"><summary>identities each result is bound to</summary>` +
    `<pre>${esc(verifyDetail(data.evidence || []))}</pre></details>` +
    ((data.reuses || []).length
      ? `<details class="fold"><summary>requests answered by an existing ` +
        `result (${data.reuses.length})</summary>` +
        `<pre>${esc(reuseDetail(data.reuses))}</pre></details>`
      : '') +
    `<div class="sbtns"><button id="sh-nover">Close<small>Nothing was run.` +
    `</small></button></div></div>`;
  $('#sh-nover').addEventListener('click', closeSheet);
  wrap.querySelectorAll('[data-ev]').forEach(btn =>
    btn.addEventListener('click', () => fetchVerifyLog(btn.dataset.ev, $('#evlog'))));
  // a verifier report's kept file, withheld: deletes bytes for good, so it
  // arms on the first click and fires on the second
  wrap.querySelectorAll('[data-rep-withhold]').forEach(btn => {
    const id = btn.dataset.repWithhold;
    const why = btn.parentElement && btn.parentElement.querySelector('input[data-rep-why]');
    wireAction(btn, `${task.file}::rep-withhold:${id}`, {
      label: 'Withhold', confirm: true,
      run: () => withholdDiagnostic(task, id, why ? why.value : ''),
    });
  });
  wrap.querySelectorAll('[data-rerun]').forEach(btn =>
    wireAction(btn, `${task.file}::verify::${btn.dataset.rerun}`, {
      label: '↻ ' + btn.dataset.rerun, confirm: 'run it?', busy: 'running…',
      run: () => rerunCheck(task, btn.dataset.rerun),
    }));
}

function verifyDetail(records) {
  const lines = [];
  for (const r of records) {
    lines.push(`evidence    ${r.id}`);
    lines.push(`check       ${r.check}${r.scope ? ' · scope ' + r.scope : ''}  ${r.checkId || ''}`);
    lines.push(`outcome     ${r.outcome}${r.reason ? ' · ' + r.reason : ''}`);
    lines.push(`command     ${r.command || '—'}`);
    lines.push(`root        ${(r.root || {}).path || '—'} (${(r.root || {}).policy || '—'})`);
    const s = r.source || {};
    lines.push(`source      ${s.commit || 'unknown'}${s.branch ? ' · ' + s.branch : ''}` +
               `  ${s.sourceId || ''}${(s.dirty || []).length ? '  +' + s.dirty.length + ' uncommitted' : ''}`);
    if (s.why) lines.push(`            ${s.why}`);
    lines.push(`policy      ${r.policyId || '—'}  (${r.policySource || '—'})`);
    if ((r.contextIds || []).length) lines.push(`context     ${r.contextIds.join(', ')}`);
    lines.push(`asked by    ${r.agent || r.actor || '—'} · intent ${r.intent || '—'}`);
    lines.push(`environment ${((r.env || {}).removed || []).length} names removed` +
               `  HOME=${(r.env || {}).home || '—'}`);
    lines.push(`evidence is ${r.location || 'local'} to this board — nothing here is committed or synced`);
    lines.push('');
  }
  return lines.join('\n') || 'no evidence recorded for this card yet';
}

/* A reuse is a request that ran nothing: an equal, current pass was
   already on record, and the request was answered with it. Each line
   names the evidence that answered and who asked this time — the
   evidence itself keeps its own requester. */
function reuseDetail(reuses) {
  return reuses.map(r =>
    `${r.check}${r.scope ? ' · scope ' + r.scope : ''} at ` +
    `${String(r.commit || '').slice(0, 12)} — asked by ` +
    `${r.agent || r.actor || '—'} (${r.intent || '—'}), answered by ` +
    `${r.reused} (run for ${(r.original || {}).agent || (r.original || {}).actor || '—'}); ` +
    `nothing was run`).join('\n');
}

async function withholdDiagnostic(task, id, reason) {
  const res = await fetch('/api/artifact/withhold', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: task.file, stage: task.stage, id, reason,
                           purpose: 'diagnostic' }),
  });
  const data = await res.json().catch(() => ({}));
  toast(res.ok ? 'file withheld — its record stays'
               : (data.error || 'nothing was withheld'), !res.ok);
  if (res.ok && S.view === 'board') verifySheet(task);
  return res.ok;
}

async function rerunCheck(task, check) {
  // ↻ is "Run again": always a new run, never an answer reused from record
  const res = await fetch('/api/verify', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: task.file, stage: task.stage, check, again: true }),
  });
  const data = await res.json();
  const ev = data.evidence || {};
  toast(res.ok
    ? `${check}: ${verifyState(ev.outcome)[2]}${ev.why ? ' — ' + ev.why : ''}`
    : (data.error || 'the check was not run'), !res.ok || ev.outcome !== 'passed');
  await loadState();
  if (S.view === 'board') verifySheet(task);
  return res.ok;
}

/* Run required checks (design #94 §10.2). Opening the sheet is a GET of
   what a click would do — the merge rule's checks at the candidate commit,
   each read as request, join, skip or not-requested-and-why — and starts
   nothing. The one armed button asks for the missing required checks, in
   order, as this person's work reading; then the same sheet follows the
   batch: preparing apart from running, what has an answer, what remains,
   and an armed Stop. Nothing is retried by itself: after a stop, a failure
   or a restart, running them again is this button, clicked again. */
const REQ_ITEM = {
  request: ['', '○', 'will be requested'],
  queued: ['', '○', 'waiting its turn'],
  requesting: ['accent', '◌', 'running now'],
  met: ['pass', '✓', 'already met — skipped'],
  joined: ['accent', '◌', 'already running — joined'],
  reused: ['pass', '✓', 'an equal current pass was on record — nothing ran'],
  problem: ['fail', '✕', 'not requested — its last result did not pass'],
  unavailable: ['fail', '!', 'not requested — your work reading may not ask for it'],
  'not-requested': ['', '·', 'not requested — the batch stopped first'],
  'other-candidate': ['fail', '!', 'ran against another commit — not this one\'s answer'],
};
let requiredTimer = null;

function requiredItemRow(i) {
  const [cls, glyph, word] = REQ_ITEM[i.state]
    || (i.state === 'error' && i.reason === 'board-restart'
      ? ['', '·', 'no result — the board restarted']
      : verifyState(i.state));
  const live = i.state === 'requesting' && i.live
    ? (i.live === 'preparing' ? 'preparing — its preparation steps are running'
                               : i.live === 'running' ? 'running the check' : 'starting')
    : word;
  const def = i.definition;
  const prep = def && def.prepare && def.prepare.length
    ? `<div class="why">preparation first: ${def.prepare.map(p =>
        `<span class="mono">${esc(p.name)}</span>${p.network ? ' (uses the network)' : ''}`).join(', ')}</div>`
    : '';
  return `<li class="check-row ${cls}" data-req="${esc(i.check)}" title="${esc(i.why || '')}">` +
    `<span class="glyph" aria-hidden="true">${glyph}</span>` +
    `<span class="name">${esc(i.check)}${i.scope ? ' · scope ' + esc(i.scope) : ' — full run'}` +
    (def && def.command ? `<small class="reqcmd">${esc(def.command)} (${esc(def.root || '')})</small>` : '') +
    `</span><span class="state">${esc(live)}` +
    (i.remedy ? `<span class="dreqfix">${esc(i.remedy)}</span>`
      : (['problem', 'failed', 'prepare-failed', 'denied', 'error', 'cancelled'].includes(i.state) && i.why
        ? `<span class="dreqfix">${esc(i.why)}</span>` : '')) +
    `</span>${prep}</li>`;
}

async function requiredSheet(task) {
  const wrap = $('#sheetwrap');
  const head = `<div class="stitle"><span>${esc(task.title)}</span>` +
    `<span class="mono">${task.number ? '#' + esc(task.number) + ' · ' : ''}run required checks</span></div>`;
  if (!wrap.classList.contains('open') || !wrap.querySelector('#reqsheet')) {
    wrap.innerHTML = `<div class="sheet" id="reqsheet">${head}<p>Reading what would be requested…</p></div>`;
    wrap.classList.add('open');
    wrap.addEventListener('click', (e) => { if (e.target === wrap) closeSheet(); });
  }
  let plan = null, error = null;
  try {
    const res = await fetch('/api/verify/required?file=' + encodeURIComponent(task.file) +
                            '&stage=' + encodeURIComponent(task.stage));
    const data = await res.json();
    if (res.ok) plan = data.plan; else error = data.error || 'the board did not answer';
  } catch { error = 'the board could not be asked'; }
  if (!wrap.querySelector('#reqsheet')) return;   // closed meanwhile
  if (!plan) {
    wrap.querySelector('#reqsheet').innerHTML = `${head}<p class="reqfail">${esc(error)}</p>` +
      `<div class="sbtns"><button id="sh-noreq">Close<small>Nothing was run.</small></button></div>`;
    $('#sh-noreq').addEventListener('click', closeSheet);
    return;
  }
  const batch = plan.batch;
  const live = batch && batch.state === 'running';
  const sum = (batch && batch.summary) || {};
  const groups = { request: [], joined: [], met: [], not: [] };
  for (const i of plan.items) {
    if (i.state === 'request') groups.request.push(i);
    else if (i.state === 'joined') groups.joined.push(i);
    else if (i.state === 'met') groups.met.push(i);
    else groups.not.push(i);
  }
  const panel = (label, right, items) => items.length
    ? `<div class="panel"><div class="phead"><span class="label">${esc(label)}</span>` +
      `<span class="r">${esc(right)}</span></div><ul class="dreqs">${items.map(requiredItemRow).join('')}</ul></div>`
    : '';
  const at = plan.head ? shortSha(plan.head) : 'no commit';
  const progress = batch
    ? `<div class="panel" id="reqprogress"><div class="phead"><span class="label">` +
      `${live ? 'Running now' : 'The last batch'}</span><span class="r">` +
      `${esc(sum.done || 0)} of ${esc(sum.total || 0)} answered · at ${esc(shortSha(batch.head))}</span></div>` +
      `<p class="why" role="status">${esc(live
        ? (sum.current ? `${sum.current} is in flight` : 'between checks') +
          (sum.remaining && sum.remaining.length ? `; still to request: ${sum.remaining.join(', ')}.` : '; nothing else remains.')
        : batch.stop ? `Stopped: ${batch.stopWhy}` : 'Finished. Each check below says what it found.')}</p>` +
      `<ul class="dreqs">${batch.items.filter(i => !['met', 'unavailable', 'problem'].includes(i.state) || i.started)
        .map(requiredItemRow).join('')}</ul></div>`
    : '';
  const n = groups.request.length;
  const canFire = !live && !plan.refusal && n > 0;
  wrap.querySelector('#reqsheet').innerHTML =
    `${head}` +
    `<p>Asks bench for every required check that has no current answer on ` +
    `<span class="mono">${esc(at)}</span>, in the merge rule's order, as your own ` +
    `request — the same as ↻ in the ◫ evidence sheet, once per check. Checks that are ` +
    `optional are never run here.</p>` +
    progress +
    (live ? '' :
      panel('Will be requested, in this order', `${n} check${n === 1 ? '' : 's'}`, groups.request) +
      panel('Already running — joined, not doubled', '', groups.joined) +
      panel('Already met on this commit — skipped', '', groups.met) +
      panel('Not requested', 'read why', groups.not)) +
    (plan.refusal && !live ? `<p class="reqfail" role="alert">${esc(plan.why)}</p>` : '') +
    `<p class="why">${esc(plan.preparation)}</p>` +
    `<p class="why">Side effects, in full: ${esc(plan.sideEffects)}</p>` +
    `<div class="sbtns">` +
    (canFire ? `<button id="sh-reqgo" class="primary">${actLabel(
      `${batch ? 'Run required checks again' : 'Run required checks'} (${n})`, 'run them?', 'requesting…')}` +
      `<small>${esc(groups.request.map(i => i.check).join(', '))}</small></button>` : '') +
    (live ? `<button id="sh-reqstop">${actLabel('Stop the batch', 'stop it?', 'stopping…')}` +
      `<small>The check in flight is cancelled; the rest are not requested.</small></button>` : '') +
    `<button id="sh-noreq">Close<small>${live ? 'The batch keeps running.' : 'Nothing was run by reading this.'}</small></button></div>`;
  $('#sh-noreq').addEventListener('click', closeSheet);
  const post = async (url, body) => {
    const res = await fetch(url, { method: 'POST', headers: SETTINGS_HEADERS, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, data };
  };
  const go = $('#sh-reqgo');
  if (go) wireAction(go, `${task.file}::required`, {
    label: 'Run required checks', confirm: 'run them?', busy: 'requesting…',
    run: async () => {
      // the head the sheet was shown goes back with the request, so a
      // commit landing meanwhile is a refusal rather than a surprise
      const { ok, data } = await post('/api/verify/required',
        { file: task.file, stage: task.stage, head: plan.head });
      toast(ok ? `requesting ${n} required check${n === 1 ? '' : 's'} on ${at}`
               : (data.error || 'nothing was requested'), !ok);
      requiredSheet(task);
      return ok;
    } });
  const stop = $('#sh-reqstop');
  if (stop) wireAction(stop, `${task.file}::required-stop`, {
    label: 'Stop the batch', confirm: 'stop it?', busy: 'stopping…',
    run: async () => {
      const { ok, data } = await post('/api/verify/required/cancel', { file: task.file });
      toast(ok ? 'stopping — the check in flight is cancelled, nothing is retried'
               : (data.error || 'not stopped'), !ok);
      requiredSheet(task);
      return ok;
    } });
  clearTimeout(requiredTimer);
  if (live) requiredTimer = setTimeout(() => {
    if ($('#reqsheet')) requiredSheet(task);
  }, 1500);
  else loadState();
}

async function replan(task) {
  const res = await fetch('/api/plan/replan', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: task.file, stage: task.stage }),
  });
  const data = await res.json();
  toast(res.ok
    ? `${task.file} replanned — from ${data.plan.startBranch}, PR into ${data.plan.prBase}`
    : (data.error || 'the card was not replanned'), !res.ok);
  await loadState();
  return res.ok;
}

function joinablePhases(task) {
  if (task.phase || task.isPhase || !task.number) return [];
  if (!['backlog', 'to-do'].includes(task.stage)) return [];
  const stage = (S.state?.board.stages || []).find(s => s.slug === 'to-do');
  return stage ? stage.tasks.filter(t => t.isPhase) : [];
}

/* Whether this card may be archived — the server's own ARCHIVE_FROM, sent
   with the state. The rule (backlog, to-do and done; never a working
   stage) is written down once, on the side that enforces it, so the chip
   and the drag-to-the-bar gesture can never offer different cards. */
function canArchive(task) {
  return (S.state.archiveFrom || []).includes(task.stage);
}

/* A card names itself once. Task files are titled `# 76 — Simplify …`, and
   the card already wears `#76` in its top row, so the title drops a
   leading number only when it is this card's own — anything else in a
   title is the author's and stays. */
function cardTitle(task) {
  const title = String(task.title || '');
  const m = title.match(/^\s*#?(\d+)\s*[—–:.\-]+\s*(.+)$/);
  if (m && task.number && canonicalNumber(m[1]) === canonicalNumber(task.number)) return m[2];
  return title;
}

/* Which chips are worth a place on the card's face. The face carries
   active work and actionable current problems — a conflict, red CI, a
   live drive or command, a phase to navigate to — and the rest live one
   labelled menu away, and in the task's detail view. A chip is never
   dropped: it moves. The ⚖ merge requirements and ◫ evidence chips say
   for themselves (`face`) whether the delivery status puts them on the
   face: a requirement that simply lies ahead is listed in the tools in
   the neutral register, and a current failure is never moved off it. */
function chipOnFace(c, task) {
  if (c.face !== undefined) return c.face;
  if (c.cls === 'bad' || c.cls === 'accent') return true;
  if (c.phase) return true;
  if (c.plan) return !task.phase && !task.isPhase;
  return false;
}

/* Focus back onto a card after a redraw rebuilt it: the visible view's
   copy, then a control inside it when one is named. A redraw is how every
   card change lands, so a keyboard user would otherwise be dropped to the
   top of the page by their own keystroke. */
function focusCard(file, inner) {
  requestAnimationFrame(() => {
    const card = [...document.querySelectorAll('.view.on article.card')]
      .find(c => c.dataset.file === file);
    if (!card) return;
    const target = (inner && card.querySelector(inner)) || card;
    target.focus();
  });
}

/* A card is built in two steps (task 130). `cardSpec` reads everything
   the card shows and writes it down as markup and a few descriptors —
   strings, no DOM — and its `sig` is all of that as one string. `mountCard`
   turns a spec into a node and wires it. So a paint can compare a card's
   new signature with the one its node was built from and keep the node
   when they are equal: same markup, same actions, same armed windows, so
   the same drawing, and nothing in it is torn down. The node's listeners
   read the spec it currently stands for (`el.__spec`), which a kept node
   is given afresh, so a click always acts on the card as it now stands. */
function cardFor(task) {
  return mountCard(cardSpec(task));
}

/* The node a keyed paint draws for `task`: the one in `pool` under its
   file when it would draw the same (given the new spec to act on), else a
   fresh one — recorded in `replaced` so focus can follow it. Board and
   Phases both paint their cards through this. */
function keyedCard(pool, task, replaced) {
  const spec = cardSpec(task);
  return keyedNode(pool, task.file, spec.sig, () => mountCard(spec),
                   (kept) => { kept.__spec = spec; }, replaced);
}

/* ── whether the work as it stands has Bench's review approval (task 152)

   Every card in review/ says it on its face, in words: being in Review is
   a stage, not an approval. The answer is the server's (`delivery.py`'s
   `review_status`, carried as `delivery[file].review`) — bound to the
   commit the card would merge — and this draws it and decides nothing.
   It sits on its own line, apart from the delivery pill, so an approval
   stays visible beside a failed check or another blocker without being
   read as permission to merge. The line is the way to the Reviews
   section, where the report and the rounds are. */
const REVIEW_GLYPH = {
  'review-approved': '✓', 'review-changes': '✕', 'review-running': '◌',
  'review-outdated': '↺', 'review-incomplete': '!', 'review-unavailable': '?',
  'review-pending': '○',
};

function reviewLine(task, d) {
  const r = d && d.review;
  if (task.stage !== 'review' || !r || !r.word) return '';
  const at = (r.word === 'review-approved' || r.word === 'review-changes') && r.head
    ? `<span class="rvh"> · ${esc(String(r.head).slice(0, 7))}</span>` : '';
  return `<button type="button" class="revstat rv-${esc(r.tone || 'neutral')}" ` +
    `data-revopen="1" data-revword="${esc(r.word)}" title="${esc(r.sentence || '')}">` +
    `<span class="rvg" aria-hidden="true">${REVIEW_GLYPH[r.word] || '·'}</span>` +
    `<span class="rvl">${esc(r.label)}</span>${at}` +
    `<span class="srx"> — ${esc(r.sentence || '')} Opens the card's Reviews.</span></button>`;
}

/* A member of an adopted phase, as its phase scope reads it (task 160):
   the server's row from the phase's lifecycle, or null. */
function memberRow(task) {
  const phase = task.phase && task.phase.file;
  const life = phase && ((S.state && S.state.phaseLifecycle) || {})[phase];
  return (life && (life.members || []).find(m => m.card === task.file)) || null;
}

function cardSpec(task) {
  const agent = agentOnTask(task.file);
  // a running phase has an agent alive on a card this view no longer draws,
  // so the phase card wears that state on its members' behalf
  const flight = task.isPhase ? phaseFlight(task) : null;
  const working = !!(agent && agent.mode !== 'review')
    || !!(flight && !flight.bad && !flight.idle);
  const failure = failedRun(task);
  // the server holds this, not the tab that clicked: while merge & clean up
  // runs, the card is mid-disassembly and hands back none of its actions
  const completing = (S.state.completing || {})[task.file];
  // The one status, from the server (task 99). The face wears it only when
  // it changes what you do next — FACE_WORDS — and never re-derives it.
  const d = deliveryOf(task);
  const tone = d ? d.status.tone : '';
  let promoted = !!(d && FACE_WORDS.has(d.status.word));
  let pill = promoted ? deliveryPill(d) : null;
  let tint = promoted && tone !== 'neutral' ? pill.tint
    : working ? 'var(--accent)' : STAGE_TINT[task.stage];
  let className = 'card'
    + (S.selected && S.selected.file === task.file ? ' selected' : '')
    + (working ? ' running' : '')
    + (promoted && tone === 'calm' ? ' verdict-good' : '')
    + (promoted && tone === 'alarm' ? ' verdict-bad' : '')
    + (failure ? ' run-failed' : '')
    + (flight && flight.bad ? ' halted' : '')
    + (completing ? ' completing' : '')
    + (task.stage === 'done' ? ' done-dim' : '');
  const draggable = !completing;
  // Two quiet readings the delivery status does not make, drawn only when
  // it has nothing to say on the face: a phase in in-progress/ nobody has
  // started (or one held) — the absence of work, in the settled register —
  // and a card in in-progress/ that nothing has touched for a day.
  if (!promoted && flight && flight.idle) {
    pill = { text: flight.pill, tint: 'var(--idle)', bg: 'var(--sunken)', title: flight.line };
    tint = 'var(--idle)';
    promoted = true;
  }
  const idle = !promoted && !agent && task.stage === 'in-progress' && !task.isPhase
    && (Date.now() / 1000 - task.mtime) / 86400 >= 1;
  if (idle) {
    pill = { text: 'idle', tint: 'var(--idle)', bg: 'var(--sunken)',
             title: `nothing has run or changed here for ${ago(task.mtime)} — `
                  + '▸ start work, or walk it back to to-do/' };
    promoted = true;
  }
  const high = (task.priority || '').toLowerCase() === 'high';
  const top = [
    `<span class="mark${working || completing ? ' breathing' : ''}" style="background:${tint}"></span>`,
    // the card's address (task 134): a real link, so a modifier-click or a
    // middle-click opens the card in a new tab; a plain click is the card's
    // own, as it always was. Not a second keyboard stop: the card is one.
    `<a class="ref" href="${esc(formatRoute({ card: cardId(task, S.state) }))}" data-cardlink ` +
      `tabindex="-1" draggable="false" title="This card's address — open it in a new tab, or copy it">` +
      `${task.number ? '#' + esc(task.number) : esc(task.file.slice(0, 10))}</a>`,
    '<span class="spacer"></span>',
    high ? '<span class="high">HIGH</span>' : '',
    task.statusMismatch ? `<span class="pill drift" title="File says ${esc(task.declaredStatus)}">drift</span>` : '',
    // a phase's list that does not resolve is an authoring mistake, and it
    // is flagged in the same breath as status drift rather than swallowed
    (task.phaseDrift || []).length
      ? `<span class="pill drift" title="${esc(task.phaseDrift.join(' · '))}">phase drift</span>` : '',
    // and so is a **Base branch:** / **PR base:** header that is not a
    // branch name, or one that contradicts the card's phase. The board
    // never guesses around it: a launch refuses until it is settled.
    (task.planDrift || []).length
      ? `<span class="pill drift" title="${esc(task.planDrift.join(' · '))}">plan drift</span>` : '',
    promoted
      ? `<span class="pill status${pill.word === 'merging' ? ' breathing' : ''}" ` +
        `style="background:${pill.bg};color:${pill.tint}"` +
        `${pill.word ? ` data-word="${esc(pill.word)}"` : ''}` +
        `${pill.title ? ` title="${esc(pill.title)}"` : ''}>${esc(pill.text)}</span>`
      : '',
  ];

  // two actions per state, max — whatever you'd actually do without opening the card
  const actions = [];
  const stillTrue = { glyph: '◔', label: 'still true?', confirm: 'check it?', busy: 'checking…',
    title: 'A read-only agent checks this task is still true of the codebase',
    run: () => fireAgent(task, '/api/agent/review') };
  if (completing) {
    // none, deliberately: an action that looks available and does nothing
    // is the same lie as a card that looks idle while its branch is deleted
  } else if (agent) {
    actions.push({ glyph: '‖', label: 'hold', confirm: 'hold it?', busy: 'holding…',
      title: 'Stop this agent — nothing is lost',
      run: () => stopAgent(agent.id) });
  } else if (task.stage === 'review' && task.pr) {
    const reviewIn = reviewIsIn(task);
    const reviewPR = { glyph: '◔', label: 'review PR', confirm: 'review it?', busy: 'reviewing…',
      title: 'A read-only agent reviews the PR and posts its verdict to GitHub',
      run: () => fireAgent(task, '/api/agent/review-pr') };
    if (reviewIn) {
      actions.push({ glyph: '↻', label: 'act on PR', confirm: 'act on it?', busy: 'acting…',
        title: 'An agent addresses the review feedback in the worktree, commits and pushes',
        run: () => fireAgent(task, '/api/agent/act-pr') }, reviewPR);
    } else {
      actions.push(reviewPR, { glyph: '⚑', label: 'copilot', confirm: 'ask copilot?', busy: 'asking…',
        title: 'Request a GitHub Copilot review on the PR',
        run: () => askCopilot(task) });
    }
  } else {
    // someone else's card is never started by accident: the action says
    // whose it is, and firing it is the deliberate takeover
    const held = task.assignee && S.state.me && task.assignee !== S.state.me
      ? task.assignee : null;
    if (task.stage === 'in-progress' && task.isPhase) {
      // every phase is the Orchestrator's once it is queued (task 188):
      // there is no ▸ run phase or phase ‖ hold — the way in is
      // Orchestrator → Phases, where it is paused and its members read
      actions.push({ glyph: '⟶', label: 'orchestrator', busy: 'opening…',
        title: 'Owned by the Orchestrator — open Orchestrator → Phases for its '
             + 'members, why each waits, its budget and the next step',
        run: () => { setView('orchestrator'); return true; } });
    } else if (task.stage === 'in-progress' && memberRow(task)
               && memberRow(task).state === 'moved-early') {
      // a member of an adopted phase sitting in in-progress/ with nothing
      // working it and its turn not come (task 160): the deliberate way
      // back, through the same move the server guards
      actions.push({ glyph: '↩', label: 'to to-do', confirm: 'move it back?', busy: 'moving…',
        title: memberRow(task).why,
        run: () => move(task.file, 'in-progress', 'to-do') });
    } else if (task.stage === 'in-progress') {
      actions.push(held
        ? { glyph: '▸', label: 'take over', confirm: `take from ${held}?`, busy: 'starting…',
            title: `${held} holds this card — starting work takes it over and reassigns it to you`,
            run: () => fireAgent(task, '/api/agent/start', { takeover: true }) }
        : { glyph: '▸', label: 'start work', confirm: 'start it?', busy: 'starting…',
            title: 'A worktree, a branch, and a headless Claude on this task',
            run: () => fireAgent(task, '/api/agent/start') });
    } else if (task.stage === 'review') {
      actions.push({ glyph: '↩', label: 'back', busy: 'moving…', title: 'Send it back for more work',
        run: () => move(task.file, 'review', 'in-progress') });
    } else if (task.stage === 'done') {
      actions.push({ glyph: '↺', label: 'reopen', busy: 'reopening…', title: 'Put it back in the queue',
        run: () => move(task.file, 'done', 'to-do') });
    } else if (joinablePhases(task).length) {
      // the small path, on the two stages that have room for it: the card
      // you decide belongs in a phase after all. It writes one line into
      // the phase card and moves nothing, so it neither costs tokens nor
      // stops work — the sheet's named choice is the confirmation.
      actions.push({ glyph: '⟶', label: 'phase', busy: 'choosing…',
        title: "Add this card to the end of a phase's list — the phases waiting in to-do/",
        run: () => { phaseSheet(task); return true; } });
    }
    // work in review with no PR: no board opens one behind your back, so
    // the card offers it instead of the relevance check
    if (task.stage === 'review' && !task.pr
        && (S.state.branches || []).includes(task.file.replace(/\.md$/, ''))) {
      actions.push({ glyph: '↑', label: 'open PR', confirm: 'open it?', busy: 'opening…',
        title: 'Push the branch and open its PR — the board does this when a card '
             + 'enters review, and this is how you ask for it afterwards',
        run: () => openPR(task) });
    } else if (actions.length < 2) {
      // two per state is the whole budget, and a halted phase has already
      // spent it on running it again and holding it
      actions.push(stillTrue);
    }
  }
  if (actions.length) className += ' has-acts';

  // the who row: who has it, plus one mono fact
  let who, initial, meta;
  if (agent) {
    who = agent.name || (agent.mode === 'review' ? 'review agent' : 'agent');
    initial = (agent.name || (agent.mode === 'review' ? 'R' : 'A')).slice(0, 1);
    meta = elapsed(agent.started) + (agent.branch ? ' · ' + agent.branch : '');
  } else {
    initial = '·';
    // a claimed card names its owner in every stage — in done/ the line is
    // history: who did this. Unclaimed cards keep the old stage vocabulary.
    if (task.assignee) { who = task.assignee; initial = task.assignee.slice(0, 1); }
    else if (task.stage === 'backlog' || task.stage === 'to-do') who = 'nobody yet';
    else if (task.stage === 'in-progress') who = 'unattended';
    else if (task.stage === 'review') who = 'needs your eyes';
    else who = 'merged';
    // when the file last changed is in the detail view; on the face it was
    // chatter, and the one reading of it that matters is the idle pill
    meta = '';
  }
  // the owner, or the agent holding it — and on a card in in-progress/
  // that nobody holds, that it is unattended. Everywhere else the column
  // already says what an unowned card is, so the row is not drawn at all.
  const showWho = !!agent || !!task.assignee || task.stage === 'in-progress';
  const extras = [];

  let liveLine = '';
  if (completing) {
    // the steps are narrated as board events against this file; the card
    // wears the latest one instead of leaving the ticker to tell the story
    liveLine = `<div class="well"><span class="lead">·</span>` +
      `<span class="wbody">${esc(completing.step || 'completing…')}<span class="caret">▌</span></span></div>`;
  } else if (agent && agent.session) {
    const run = S.running[agent.session];
    const smeta = sessionMeta(agent.session);
    const last = run || (smeta && smeta.lastSummary ? { summary: smeta.lastSummary, ok: null } : null);
    if (last && last.summary) {
      const active = run || (smeta && smeta.status === 'active');
      liveLine = `<div class="well"><span class="lead">·</span>` +
        `<span class="wbody">${esc(last.summary)}${active ? '<span class="caret">▌</span>' : ''}</span></div>`;
    }
    // what the run last *said* sits above what it last *did*: two facts
    // with two clocks, and only the first is about progress
    liveLine = progressWell(task, agent) + liveLine;
  } else if (agent) {
    liveLine = `<div class="well"><span class="lead">·</span><span class="wbody">warming up<span class="caret">▌</span></span></div>`;
  } else if (failure) {
    // "API Error: 500" one hover away instead of buried in a log file: the
    // line the run died on here, the whole excerpt on hover and in the sheet
    liveLine = `<div class="well bad" title="${esc(failure.excerpt)}">` +
      `<span class="lead">·</span><span class="wbody">rc=${esc(failure.rc)} · ` +
      `${esc(whyFailed(failure))}</span></div>`;
  } else if (flight) {
    // the member in flight, on the card that stands for it — the same line
    // the header chip carries, at the altitude a person is already reading
    liveLine = `<div class="well${flight.bad ? ' bad' : ''}"><span class="lead">·</span>` +
      `<span class="wbody">${esc(flight.line)}` +
      `${flight.bad || flight.idle ? '' : '<span class="caret">▌</span>'}</span></div>`;
  }

  // tool chips: destinations, not statuses — they live in the card's footer
  const prState = (S.state.prs || {})[task.file];
  const detail = prState && prState.detail ? prState.detail : 'open the PR';
  const hasBranch = (S.state.branches || []).includes(task.file.replace(/\.md$/, ''));
  const chips = [];
  // what the phase card now stands for: its progress, and the way through
  // to the cards themselves — its own sheet lists them in run order with
  // the stage each is in, which is where they are read until the Phases
  // view draws them
  if (task.isPhase) {
    chips.push({ label: phaseSummary(task), pre: '⟶', cls: '', phase: task.file,
      title: 'The cards in this phase, in run order — they are not drawn on '
           + 'the board while it holds them' });
  }
  // where this card sits in its phase — derived from the phase card's own
  // list, and a destination like the rest of the row: it opens that card
  if (task.phase) {
    // the title ellipsizes to the card's width; the position never does
    const phaseName = phaseFullName(task.phase);
    const pos = `${task.phase.index}/${task.phase.total}`;
    chips.push({ label: `${phaseName} ${pos}`,
      html: `<span class="clab">${esc(phaseName)}</span><span class="cpos">${esc(pos)}</span>`,
      pre: '⟶', cls: '', phase: task.phase.file,
      title: `Card ${task.phase.index} of ${task.phase.total} in phase `
           + `${task.phase.number ? '#' + task.phase.number + ' — ' : ''}${task.phase.title}` });
  }
  // where this card's work starts and what its PR targets, when that is
  // not simply the application default — a destination like the rest of
  // the row, opening the preflight itself. A board of ordinary cards on
  // the default has no such chip anywhere.
  const planned = (S.state.plans || {})[task.file];
  // A stacked card's chip says where it sits instead — the server's own
  // words (`on #56 · 2/3`, `step 2` where the stack branches), so the
  // page counts nothing (task 117). Plan drift still wins: a launch is
  // going to refuse until it is settled.
  const stack = (S.state.stacks || {})[task.file];
  const drift = planned && (planned.drift || []).length;
  if (stack && stack.chip && !drift) {
    chips.push({ label: stack.chip, pre: '⌖', plan: true,
      cls: stack.edge && stack.edge.tone === 'alarm' ? 'bad' : '',
      title: stackChipTitle(stack) });
  } else if (planned) {
    chips.push({ label: planChipLabel(planned), html: planChipHtml(planned), pre: '⌖',
      cls: drift ? 'bad' : '', plan: true,
      title: planChipTitle(planned) });
  }
  // what a launch on this card would be allowed to do, and which files it
  // would be given — a destination like the rest of the row, opening the
  // profile preflight. Present only where there is something to say: a
  // board resolving on the legacy settings has no such chip anywhere, and
  // a policy document that does not parse wears the alarm.
  const policy = S.state.policy;
  if (policy && policy.kind === 'broken') {
    chips.push({ label: 'policy', pre: '⚿', cls: 'bad', profile: true,
      title: policy.problem || 'the profile document does not resolve' });
  } else if (policy && policy.kind === 'file') {
    chips.push({ label: 'launch check', pre: '⚿', cls: '', profile: true,
      title: `What a launch on this card would ride — branch, PR target, `
           + `harness and model, what it may run, where it is meant to write `
           + `(intended scope, no OS isolation), which files it is given and `
           + `what is blocked. From ${policy.identity}.` });
  } else if (['in-progress', 'review'].includes(task.stage) && !completing) {
    // no profile chip on this board, but a card about to be launched still
    // gets its launch check — in the tools, opening the same sheet
    chips.push({ label: 'launch check', pre: '▸', cls: '', profile: true, face: false,
      title: 'What a launch on this card would ride — branch, PR target, harness, '
           + 'model, access and anything that stops it. Opening it starts nothing.' });
  }
  // what bench's own verification runs found on this card — a
  // destination like the rest of the row, opening the evidence. Present
  // only where there is evidence or a check that could have produced
  // some, so a board nobody declared one on has no such chip anywhere.
  const verified = verifyChip(task);
  if (verified) chips.push(verified);
  // whether this card may actually be merged, and what is in the way —
  // a destination like the rest of the row, opening the gate. Present
  // only where this board declares a gate, so a board that declares
  // none has no such chip anywhere.
  const gate = gateChip(task);
  if (gate) chips.push(gate);
  // where a PR review of this card ran and which revision it was of — a
  // destination like the rest of the row, opening the record. Present
  // only on cards bench has pinned a checkout for.
  const reviewed = reviewChip(task);
  if (reviewed) chips.push(reviewed);
  // how many tracked criteria a named person has accepted, and how many
  // review findings are still open (task 81) — each a link to its section
  // of the detail view. Never a completion ratio: the word is "accepted".
  for (const c of acceptanceChips(task)) chips.push(c);
  if (prState && prState.ci) {
    chips.push({ label: 'CI', glyph: { pass: '✓', fail: '✕', running: '◌' }[prState.ci],
      cls: { pass: 'ok', fail: 'bad', running: 'accent' }[prState.ci], title: detail });
  }
  if (prState && prState.conflicts) {
    const into = prState.wantBase || 'main';
    chips.push({ label: 'conflicts', glyph: '✕', cls: 'bad',
      title: `GitHub cannot merge this into ${into} — ↻ act on PR can attempt a resolution merge` });
  }
  // a PR open against a branch this card never planned: reported, never
  // retargeted, because which of the two readings is right needs a person
  if (prState && prState.baseMismatch) {
    chips.push({ label: `into ${prState.baseMismatch}`, glyph: '✕', cls: 'bad',
      title: `This PR is open against ${prState.baseMismatch}, and the card's `
           + `recorded plan targets ${prState.wantBase}. Nothing was retargeted — `
           + 'settle which base is right before merging.' });
  }
  if (prState && prState.copilot) {
    chips.push({ label: 'copilot',
      glyph: { asked: '◌', approved: '✓', changes: '✕', commented: '·' }[prState.copilot],
      cls: { asked: 'accent', approved: 'ok', changes: 'bad', commented: '' }[prState.copilot],
      title: 'Copilot: ' + ({ asked: 'review requested', approved: 'approved',
        changes: 'changes requested', commented: 'commented' }[prState.copilot]) });
  }
  if (task.pr) chips.push({ label: 'PR', glyph: '↗', cls: '', href: task.pr, title: detail });
  let driveWell = '';
  // the drive and the project's commands run against a worktree this card
  // is in the middle of removing: no chip offers either while it does
  if (task.stage === 'review' && !completing) {
    const d = S.state.drive;
    if (d && d.task === task.file && (d.status === 'up' || d.status === 'starting')) {
      if (d.status === 'up') chips.push({ label: 'open', glyph: '✳', cls: 'accent',
        href: d.url || '#', title: "the app, running this task's code" });
      else {
        chips.push({ label: 'starting', glyph: '◌', cls: 'accent',
          title: 'the driver is bringing the app up — progress below' });
        driveWell = `<div class="well"><span class="lead">✳</span>` +
          `<span class="wbody">${esc(d.line || 'driver starting…')}<span class="caret">▌</span></span></div>`;
      }
      chips.push({ label: 'park', glyph: '‖', cls: '', act: 'park', title: 'Stop the drive' });
    } else if (d && d.task === task.file && d.status === 'refused') {
      chips.push({ label: 'drive', glyph: '✕', cls: 'bad', act: 'go',
        title: 'The last drive did not come up — click to try again' });
      driveWell = `<div class="well bad"><span class="lead">✳</span>` +
        `<span class="wbody">${esc(d.reason || 'the driver gave up — see the drive log')}</span></div>`;
    } else if (!hasBranch && !task.pr) {
      chips.push({ label: 'no branch', glyph: '', cls: 'dim',
        title: 'No work attached: no agent ran on this task, so there is no branch to PR, review or drive. ▸ start work runs from in-progress.' });
    } else if (S.state.hasDriver && hasBranch) {
      // a resting drive is a tool, not a state: it lives in the ⋯ menu,
      // and only a running or refused drive earns the face
      chips.push({ label: 'drive', glyph: '✳', cls: 'accent', act: 'go', face: false,
        title: "Launch the app locally from this task's worktree" });
    } else if (!S.state.hasDriver) {
      chips.push({ label: 'no driver', glyph: '', cls: 'dim',
        title: 'No driver to launch the local app — create manager/local/driver/start (an executable; see manager/core/driver.example/).' });
    }
  }
  // project commands run against this task's worktree
  if (hasBranch && !completing && ['in-progress', 'review'].includes(task.stage)) {
    for (const cmd of (S.state.commands || [])) {
      const running = (S.state.commandRuns || []).some(r => r.task === task.file && r.name === cmd.name);
      if (running) {
        chips.push({ label: cmd.name, glyph: '◌', cls: 'accent',
          title: `${cmd.name} is running — the ticker narrates the ending` });
      } else {
        chips.push({ label: cmd.name, glyph: '$', cls: '', cmd: cmd.name,
          title: cmd.help || `run local/commands/${cmd.name} against this task's worktree` });
      }
    }
  }
  // archiving, on the card instead of the length of the board away: the
  // tray's own glyph, and only on the stages the tray itself accepts —
  // a working card is finished or walked back, never tidied away
  if (canArchive(task)) {
    // a done card has nothing left to do but this, so there it is on the
    // face (task 85); every other stage keeps it in the ⋯ tools
    chips.push({ label: 'archive', pre: '⌸', cls: 'arch', arch: true,
      face: task.stage === 'done',
      title: 'Archive this card — out of every column, never deleted. ⌘Z brings it back.' });
  }
  const chipHtml = (c) => {
        const g = c.glyph ? `<span class="g2">${c.glyph}</span>` : '';
        const p = c.pre ? `<span class="g2">${c.pre}</span>` : '';
        // the text that may ellipsize, apart from the glyphs that may not
        const l = c.html || `<span class="clab">${esc(c.label)}</span>`;
        if (c.arch) return `<button type="button" class="chip2 ${c.cls}" data-arch="1" title="${esc(c.title)}">` +
          `${p.replace('<span ', '<span aria-hidden="true" ')}${actLabel(c.label, 'archive it?', 'archiving…')}` +
          `<span class="srx"> ${esc(task.number ? '#' + task.number : task.file)}</span></button>`;
        if (c.href) return `<a class="chip2 ${c.cls}" href="${esc(c.href)}" target="_blank" rel="noopener" title="${esc(c.title)}">${p}${l}${g}</a>`;
        if (c.act) return `<button class="chip2 ${c.cls}" data-drive="${c.act}" title="${esc(c.title)}">${p}${l}${g}</button>`;
        if (c.phase) return `<button class="chip2 ${c.cls}" data-phase="${esc(c.phase)}" title="${esc(c.title)}">${p}${l}${g}</button>`;
        if (c.plan) return `<button class="chip2 ${c.cls}" data-plan="1" title="${esc(c.title)}">${p}${l}${g}</button>`;
        if (c.profile) return `<button class="chip2 ${c.cls}" data-profile="1" title="${esc(c.title)}">${p}${l}${g}</button>`;
        if (c.verify) return `<button class="chip2 ${c.cls}" data-verify="1" title="${esc(c.title)}">${p}${l}${g}</button>`;
        if (c.gate) return `<button class="chip2 ${c.cls}" data-gate="1" title="${esc(c.title)}">${p}${l}${g}</button>`;
        if (c.review) return `<button class="chip2 ${c.cls}" data-review="1" title="${esc(c.title)}">${p}${l}${g}</button>`;
        if (c.section) return `<button class="chip2 ${c.cls}" data-section="${esc(c.section)}" title="${esc(c.title)}">${p}${l}${g}</button>`;
        if (c.cmd) return `<button class="chip2 ${c.cls}" data-cmd="${esc(c.cmd)}" title="${esc(c.title)}">${actLabel(c.label, 'run it?', 'running…')}${g}</button>`;
        return `<span class="chip2 ${c.cls}" title="${esc(c.title)}">${p}${l}${g}</span>`;
  };
  // the face carries the exceptions; every chip, the face's included, is
  // in the card's tools — opened from the ⋯ button, and drawn in full in
  // the detail view, so nothing a chip did is lost by not being on the face
  const face = chips.filter(c => chipOnFace(c, task));
  const chipRow = face.length
    ? '<div class="chiprow">' + face.map(chipHtml).join('') + '</div>'
    : '';
  const ref = task.number ? '#' + task.number : task.file.slice(0, 10);
  const menuOpen = S.cardMenu === task.file;
  const tools = chips.map(c => chipHtml(chipOnFace(c, task)
    ? { ...c, cls: `${c.cls} onface` } : c)).join('');

  const html =
    `<div class="toprow">${top.join('')}</div>` +
    `<div class="title">${esc(cardTitle(task))}</div>` +
    reviewLine(task, d) +
    (showWho
      ? `<div class="whorow"><span class="initial">${esc(initial)}</span><span class="who">${esc(who)}</span>` +
        modelChip(agent) +
        `<span class="meta">${esc(meta)}${extras.length ? ' · ' + extras.join(' · ') : ''}</span></div>`
      : '') +
    chipRow + driveWell + liveLine +
    `<div class="cardtools${menuOpen ? ' open' : ''}" role="group" ` +
    `aria-label="${esc(`Tools for ${ref}`)}"><span class="tlabel">tools</span>` +
    `<span class="tacts"></span>${tools}</div>`;
  // a done card whose only tool is the archive on its face has nothing
  // left for a menu, so it gets none rather than an empty one
  const more = !!chips.length && (task.stage !== 'done' || chips.some(c => !chipOnFace(c, task)));
  // every action this card's buttons are keyed by, so an armed or busy
  // window that opens or closes is a change in what the card draws
  const keys = actions.map(a => `${task.file}::${a.label}`);
  if (chips.some(c => c.arch)) keys.push(`${task.file}::archive`);
  for (const c of chips) if (c.cmd) keys.push(`${task.file}::$${c.cmd}`);
  const sig = [
    task.file, task.stage, className, draggable, html, menuOpen, more,
    actions.map(a => [a.glyph, a.label, a.confirm || '', a.busy || '', a.title]
      .join('\u0002')).join('\u0003'),
    keys.map(k => phaseOf(S.acts, k)).join(','),
  ].join('\u0001');
  return { task, agent, actions, className, draggable, html, menuOpen, more, ref, sig };
}

/* A spec, made into a card node. Every listener reads `el.__spec` when it
   fires rather than closing over the spec it was built from, so a node a
   later paint keeps acts on the card as that paint read it. */
function mountCard(spec) {
  const el = document.createElement('article');
  el.__spec = spec;
  el.__sig = spec.sig;
  const cur = () => el.__spec.task;
  // `task` is the card as first built, read here for its file — the key
  // a kept node never changes; everything else is read through cur()
  const { actions, ref, menuOpen, task } = spec;
  const file = task.file;
  el.className = spec.className;
  el.draggable = spec.draggable;
  el.innerHTML = spec.html;
  el.dataset.file = file;
  // the card itself is reachable: Tab lands on it, Enter or Space opens
  // its detail view, and focus-within reveals the same actions hover does
  el.tabIndex = 0;
  el.addEventListener('keydown', (e) => {
    if (e.target === el && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      showDetail(cur());
    } else if (e.key === 'Escape' && S.cardMenu === file) {
      e.stopPropagation();
      S.cardMenu = null;
      renderCards();
      focusCard(file, '[data-more]');
    }
  });
  // the address in the top row: a plain click opens the card here, as a
  // click anywhere on it does; any other click is the browser's
  const link = el.querySelector('a[data-cardlink]');
  if (link) link.addEventListener('click', (e) => {
    if (plainClick(e)) e.preventDefault();
    else e.stopPropagation();
  });
  // a click between two tools is not a click on the card
  el.querySelector('.cardtools').addEventListener('click', (e) => e.stopPropagation());
  el.querySelectorAll('a.chip2').forEach(a =>
    a.addEventListener('click', (e) => e.stopPropagation()));
  el.querySelectorAll('[data-phase]').forEach(btn =>
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const phase = findTask(btn.dataset.phase);
      if (phase) showDetail(phase);
    }));
  el.querySelectorAll('[data-plan]').forEach(btn =>
    btn.addEventListener('click', (e) => { e.stopPropagation(); planSheet(cur()); }));
  el.querySelectorAll('[data-profile]').forEach(btn =>
    btn.addEventListener('click', (e) => { e.stopPropagation(); profileSheet(cur()); }));
  el.querySelectorAll('[data-verify]').forEach(btn =>
    btn.addEventListener('click', (e) => { e.stopPropagation(); verifySheet(cur()); }));
  el.querySelectorAll('[data-gate]').forEach(btn =>
    btn.addEventListener('click', (e) => { e.stopPropagation(); gateSheet(cur()); }));
  el.querySelectorAll('[data-review]').forEach(btn =>
    btn.addEventListener('click', (e) => { e.stopPropagation(); reviewSheet(cur()); }));
  // the review line (task 152) opens the card on its Reviews, as a
  // section chip would — its own attribute, because it is not a count
  el.querySelectorAll('[data-revopen]').forEach(btn =>
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      S.dtab[file] = 'reviews';
      if (S.selected && S.selected.file === file) { renderDrawer(); syncRoute(); }
      else showDetail(cur());
    }));
  el.querySelectorAll('[data-section]').forEach(btn =>
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      S.dtab[file] = btn.dataset.section;
      if (S.selected && S.selected.file === file) { renderDrawer(); syncRoute(); }
      else showDetail(cur());
    }));
  el.querySelectorAll('[data-drive]').forEach(btn =>
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (btn.dataset.drive === 'go') startDrive(cur()); else parkDrive();
    }));
  el.querySelectorAll('[data-arch]').forEach(btn => {
    btn.addEventListener('click', (e) => e.stopPropagation());
    // pressing the chip is not picking up the card: a press that wanders a
    // few pixels before its release archives or does nothing, never drags
    btn.addEventListener('pointerdown', () => {
      el.dataset.nodrag = '1';
      const release = () => {
        delete el.dataset.nodrag;
        window.removeEventListener('pointerup', release, true);
        window.removeEventListener('pointercancel', release, true);
      };
      window.addEventListener('pointerup', release, true);
      window.addEventListener('pointercancel', release, true);
    });
    wireAction(btn, `${task.file}::archive`, {
      label: 'archive', confirm: 'archive it?', busy: 'archiving…',
      run: () => archiveCard(file, cur().stage),
    });
  });
  el.querySelectorAll('[data-cmd]').forEach(btn => {
    btn.addEventListener('click', (e) => e.stopPropagation());
    const name = btn.dataset.cmd;
    wireAction(btn, `${task.file}::$${name}`, {
      label: name, confirm: 'run it?', busy: 'running…',
      run: () => runCommand(cur(), name),
    });
  });

  if (actions.length) {
    const slot = document.createElement('span');
    slot.className = 'hoveracts';
    // one guard for the whole slot: clicks in its padding or in the gap
    // between buttons die here instead of opening the card's detail
    slot.addEventListener('click', (e) => e.stopPropagation());
    actions.forEach((act, i) => {
      // the run is the one the card's current spec holds: the same action
      // in the same place (the signature names every action), as the
      // latest paint read the card
      const live = { label: act.label, confirm: act.confirm, busy: act.busy,
                     run: () => (el.__spec.actions[i] || act).run() };
      const btn = document.createElement('button');
      btn.innerHTML = `<span class="g">${act.glyph}</span>${actLabel(act.label, act.confirm, act.busy)}`;
      btn.title = act.title;
      wireAction(btn, `${task.file}::${act.label}`, live);
      slot.appendChild(btn);
      // the same action, keyed the same, in the tools: the detail view
      // carries it there, and an armed window is one window wherever the
      // second click lands
      const twin = document.createElement('button');
      twin.className = 'chip2 onface';
      twin.innerHTML = `<span class="g2">${act.glyph}</span>${actLabel(act.label, act.confirm, act.busy)}`;
      twin.title = act.title;
      // named, so a detail view redrawn under a focused twin can find it
      // again (drawerFocusKey reads data-* attributes)
      twin.dataset.toolAct = act.label;
      wireAction(twin, `${task.file}::${act.label}`, live);
      el.querySelector('.cardtools .tacts').appendChild(twin);
    });
    el.querySelector('.toprow').appendChild(slot);
  }
  // the tools menu: one labelled control on every card that has anything
  // in it, at the end of the top row, so the face stays quiet and nothing
  // a chip used to do is more than one keystroke away
  if (spec.more) {
    const more = document.createElement('button');
    more.className = 'moretools';
    more.dataset.more = file;
    more.textContent = '⋯';
    more.title = 'Tools — profile, branch, PR, checks, drive, archive';
    more.setAttribute('aria-label', `Tools for ${ref}`);
    more.setAttribute('aria-expanded', String(menuOpen));
    more.addEventListener('click', (e) => {
      e.stopPropagation();
      S.cardMenu = S.cardMenu === file ? null : file;
      renderCards();
      focusCard(file, S.cardMenu ? '.cardtools.open .chip2:not(.onface)' : '[data-more]');
    });
    el.querySelector('.toprow').appendChild(more);
  }

  el.addEventListener('dragstart', (e) => {
    if (el.dataset.nodrag) { e.preventDefault(); return; }
    const task = cur();
    el.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('application/json', JSON.stringify({ file: task.file, from: task.stage }));
    if (canArchive(task)) {
      S.dragging = { file: task.file, from: task.stage };
      renderBar();
    }
  });
  el.addEventListener('dragend', () => {
    el.classList.remove('dragging');
    S.dragging = null; S.dockHot = false; renderBar();
  });
  el.addEventListener('click', () => showDetail(cur()));
  return el;
}

/* arm-then-fire is the contract for anything that costs tokens or stops
   work. This one state machine walks every action button — hover actions
   and $-command chips alike — through rest → armed → busy. The truth
   lives in S.acts, keyed by task file + action, because cards are torn
   down and rebuilt on every SSE render: a rebuild mid-window re-applies
   the same picture (the drain bar resumes via a negative delay), and a
   second click keeps its meaning across the redraw. The machine itself
   is lib/arm.js (task 125): one store, one timeout per phase, shared
   with the stack sheet's confirmation. */
const FIRE_TIMEOUT_MS = BUSY_MS;
const acts = arming({ store: S.acts });

function actLabel(rest, confirm, busy) {
  return `<span class="actlbl"><span class="l-rest">${esc(rest)}</span>` +
    (confirm ? `<span class="l-arm">${esc(confirm)}</span>` : '') +
    `<span class="l-busy">${esc(busy || rest)}</span></span>`;
}

/* An armed window belongs to the card it was opened on. A card can leave
   the board while one is open — archived from another board, or moved by
   hand on disk — and the window must go with it, so a card that comes
   straight back (⌘Z, or a teammate's undo) is never found already armed
   by a click nobody made against the card as it now stands. Keys are
   `<file>::<action>`, and a task file never contains `::`. */
/* Act keys live in two namespaces and the sweep has to know which is
   which. A card's actions are keyed by its filename, so an armed window
   goes when the card does — archived from another board, or moved by hand
   on disk. The board's own actions — the readiness inspector's active
   stages — belong to the board rather than to any card, so they are
   keyed `board::` and the sweep leaves them alone. A key that is neither
   is a bug, and it is dropped. */
const BOARD_ACT = 'board::';

function forgetActsOfVanishedCards() {
  const live = new Set(allTasks().map(t => t.file));
  acts.sweep(key => key.startsWith(BOARD_ACT) || live.has(key.split('::')[0]));
}

function wireAction(btn, key, act) {
  const phase = acts.phase(key);
  if (phase === 'busy') lockAction(btn);
  else if (phase === 'armed') armAction(btn, key, acts.remaining(key));
  btn.addEventListener('click', () => {
    if (btn.disabled) return;
    if (act.confirm && acts.phase(key) !== 'armed') armAction(btn, key, ARM_MS);
    else fireAction(btn, key, act);
  });
}

function armAction(btn, key, remaining) {
  // a rebuilt button rejoins the window it found; a first click opens one
  if (acts.phase(key) !== 'armed') acts.arm(key, remaining, () => btn.classList.remove('armed'));
  // the CSS drain animation is ARM_MS long; a rebuilt button rejoins it
  // partway through with a negative delay instead of starting over
  btn.style.setProperty('--arm-delay', `${remaining - ARM_MS}ms`);
  btn.classList.add('armed');
  setTimeout(() => { if (acts.phase(key) !== 'armed') btn.classList.remove('armed'); }, remaining);
}

function lockAction(btn) {
  btn.classList.remove('armed');
  btn.classList.add('busy');
  btn.disabled = true;
}

async function fireAction(btn, key, act) {
  lockAction(btn);
  // client-side optimism needs an honest exit: if neither the response
  // nor an SSE redraw arrives, come back to rest loudly — never silently,
  // and never by sending the request again
  acts.fire(key, () => {
    toast(`${act.label} got no answer in ${FIRE_TIMEOUT_MS / 1000}s — not fired again; check the board`, true);
    scheduleRender();
  });
  let ok;
  try { ok = await act.run() !== false; }
  catch (e) { ok = false; toast(`${act.label} failed — ${e.message || 'no response'}`, true); }
  if (acts.settle(key)) {
    btn.disabled = false;
    btn.classList.remove('busy');
    // a successful run usually re-rendered the card already (loadState);
    // this pass unlocks any survivor whose card did not change
    scheduleRender();
  }
  return ok;
}

async function fireAgent(task, url, extra) {
  toast(`starting on ${task.file}…`);
  const res = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: task.file, stage: task.stage, ...(extra || {}) }),
  });
  const data = await res.json();
  if (!res.ok) {
    toast(data.error || 'that did not start', true);
    // refused for its information (task 132): open where it is fixed
    if (data.code === 'information') openInputs(task.file);
    return false;
  }
  const who = data.agent.name || 'An agent';
  toast(url.endsWith('act-pr')
    ? `${who} is acting on the review of ${task.file}'s PR`
    : url.endsWith('review-pr')
      // which revision, because that is what the verdict will be about and
      // the click is the moment it was decided
      ? `${who} is reviewing ${task.file}'s PR at ` +
        `${((data.agent.review || {}).head || 'an unrecorded commit').slice(0, 12)}`
      : data.agent.mode === 'review'
        ? `${who} is checking ${task.file} is still true of the codebase`
        : `${who} is on it — branch ${data.agent.branch}`);
  await loadState();
  return true;
}

async function runCommand(task, name) {
  const res = await fetch('/api/command/run', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, file: task.file }),
  });
  const data = await res.json();
  toast(res.ok ? `${name} running on ${task.file} — the ticker narrates the ending`
               : (data.error || `${name} did not start`), !res.ok);
  await loadState();
  return res.ok;
}

async function startDrive(task) {
  const res = await fetch('/api/drive/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: task.file }),
  });
  const data = await res.json();
  toast(res.ok ? 'Driver starting — progress shows on the card'
               : (data.error || 'the driver did not start'), !res.ok);
  loadState();
}

async function parkDrive() {
  const res = await fetch('/api/drive/stop', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  const data = await res.json();
  toast(res.ok ? 'Parked' : (data.error || 'could not park'), !res.ok);
  loadState();
}

async function openPR(task) {
  const res = await fetch('/api/pr/open', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: task.file }),
  });
  const data = await res.json();
  toast(res.ok ? `PR opened for ${task.file}` : (data.error || 'the PR did not open'), !res.ok);
  await loadState();
  return res.ok;
}

async function askCopilot(task) {
  const res = await fetch('/api/pr/copilot', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: task.file }),
  });
  const data = await res.json();
  toast(res.ok ? `Copilot asked to review ${task.file}'s PR`
               : (data.error || 'Copilot request failed'), !res.ok);
  return res.ok;
}

async function move(file, from, to) {
  // finishing a task with work attached is a decision, not just a drag
  if (to === 'done') {
    const task = findTask(file);
    const stem = file.replace(/\.md$/, '');
    if (task && (task.pr || (S.state.branches || []).includes(stem))) {
      completeSheet(task, from);
      return true;
    }
  }
  return rawMove(file, from, to);
}

/* Open a card's detail view on its Inputs tab — where a refusal for the
   card's information is fixed (task 132). */
function openInputs(file) {
  const task = findTask(file);
  if (!task) return;
  S.dtab[file] = 'inputs';
  showDetail(task);
}

async function rawMove(file, from, to) {
  const res = await fetch('/api/move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file, from, to }),
  });
  const data = await res.json();
  // refused for its information (task 132): the refusal opens the place
  // it is fixed — the card's Inputs tab
  if (!res.ok && data.code === 'information') openInputs(file);
  if (!res.ok) { toast(data.error || 'move failed', true); await loadState(); return false; }
  toast(`${file} → ${to}/`);
  if (S.selected && S.selected.file === file) S.selected = data.task;
  await loadState();
  return true;
}

function closeSheet() {
  $('#sheetwrap').classList.remove('open');
  $('#sheetwrap').innerHTML = '';
  // a place belongs to the sheet that was open: the next one opens at the top
  delete scrollMarks['v:sheet:doctor'];
  delete scrollMarks['v:sheet:launch'];
  // the configuration and launch checks give focus back to what opened them
  let back = S.sheetReturn;
  S.sheetReturn = null;
  // a delivery action that opened it may have been redrawn meanwhile by a
  // live update: the same action is found again by its slot
  if (back && !back.isConnected && back.dataset && back.dataset.dslot) {
    back = document.querySelector(`#drawerbody [data-dslot="${back.dataset.dslot}"]`);
  }
  if (back && back.isConnected && back.focus) back.focus();
}

/* What merge & clean up would do for this card, in words, from the facts
   the server resolves with the same authorities the completion reads: the
   application merge mode (never ticket sync on its own), the recorded
   target, the remote, a refusal, an unfinished earlier attempt. */
function completionWords(facts, task, driving, swept) {
  const branch = esc(facts.branch), target = esc(facts.target);
  const remote = esc(facts.remote || 'the remote');
  const park = driving ? 'Park the drive, then m' : 'M';
  const after = ` Only then are the worktree and branches removed and the card moved to done/` +
    (swept ? ` — the ${swept} card${swept === 1 ? '' : 's'} this phase merged go to done/ with it, ` +
             `and their worktrees and branches go too` : '') +
    `. If ${remote}/${target} does not hold the work afterwards, nothing is cleaned up and ` +
    `the card stays in review/ to be retried.`;
  let ship;
  if (facts.mode === 'origin') {
    ship = `${park}erge the PR on GitHub into ${target}, then check ${remote}/${target} holds ` +
      `the work.${after} Local ${target} ` +
      (facts.ticketSync && !facts.ticketsSeparate
        ? 'fast-forwards on the next sync beat.' : 'fast-forwards to the merge afterwards.');
  } else if (facts.publication === 'push') {
    ship = `${park}erge ${branch} into ${target} in this checkout and push ${target} to ` +
      `${remote}${task.pr ? ' (marks the PR merged)' : ''}.${after}`;
  } else {
    ship = `${park}erge ${branch} into ${target} in this checkout. This application has no ` +
      `remote, so nothing is pushed or merged on GitHub — the work is complete locally only. ` +
      `Then the worktree and branch are removed and the card moves to done/.`;
  }
  // a separate ticket repository syncs the card, never the code
  const tickets = facts.ticketsSeparate && facts.ticketSync
    ? `Ticket sync publishes this card's move to the separate ticket repository only; it does ` +
      `not merge or push application code.` : '';
  return { ship, tickets };
}

/* What an unfinished completion is (task 166), from the server's record and
   nothing else: the outcome in words, the one next step, and — folded —
   the identities kept apart (the card's head, the merge this checkout
   made, the remote's tip) with the operation that left them. The text is
   the server's, already scrubbed; the page adds no reading of its own. */
const PUBLICATION_OUTCOMES = {
  rejected: 'the remote refused the push',
  diverged: "the remote's target has commits this checkout's does not",
  unavailable: 'the remote could not be read',
  unknown: 'whether the remote holds the merge is not established',
};

function unfinishedPanel(pending) {
  const short = (sha) => esc(String(sha || '').slice(0, 12) || '—');
  const outcome = pending.outcome ||
    (pending.publication === 'failed' ? 'rejected' : 'unknown');
  const where = pending.mode === 'origin' ? 'on GitHub' : 'in this checkout';
  const headline = pending.deliveredElsewhere
    ? 'Delivered elsewhere — the merge made here is not published'
    : `Merged, not published — ${PUBLICATION_OUTCOMES[outcome] || outcome}`;
  const happened = pending.deliveredElsewhere
    ? `${esc(pending.remote || 'the remote')}/${esc(pending.target)} already holds ` +
      `${short(pending.commit)} through another merge, so this card's work is delivered. ` +
      `The merge this checkout made, ${short(pending.merge)}, is not on the remote.`
    : `${esc(pending.branch)} was merged into ${esc(pending.target)} ${where} at ` +
      `${short(pending.commit)}, and ${esc(pending.remote || 'the remote')}/` +
      `${esc(pending.target)} does not hold ${pending.merge && pending.merge !== pending.commit
        ? `the merge ${short(pending.merge)}` : 'it'}: ${esc(pending.detail || pending.publication)}.`;
  const next = pending.action ||
    (pending.diverged
      ? `${pending.remote}/${pending.target} has commits this checkout does not — bring them ` +
        `into ${pending.target} (pull, then merge; never force), then retry.`
      : 'Resolve what the remote refused (a protection rule, access, the network), then retry.');
  const op = pending.operation || {};
  const history = (pending.history || []).map(h =>
    `<li><span class="mono">${esc(h.id || '')}</span> · ${esc(h.source || 'api')}` +
    ` · ${h.actor ? esc(h.actor) : 'actor not verified'} · ${esc(h.outcome || h.publication || '')}</li>`).join('');
  return `<div class="panel bad" data-outcome="${esc(outcome)}"><div class="phead"><span class="label">` +
    `${esc(headline)}</span></div>` +
    `<div class="check-row fail"><span class="glyph">✕</span><span class="name">what happened</span>` +
    `<span class="state">${happened} Nothing was rolled back, nothing was cleaned up, and ` +
    `the card stayed in review/.</span></div>` +
    `<div class="check-row"><span class="glyph">·</span><span class="name">what to do</span>` +
    `<span class="state">${esc(next)} The retry checks the remote first and does not merge twice.` +
    `</span></div>` +
    `<details class="cmp-evidence"><summary>Identities and evidence</summary>` +
    `<dl class="cmp-ids">` +
    `<dt>card head</dt><dd class="mono">${short(pending.commit)}</dd>` +
    `<dt>merge made here</dt><dd class="mono">${pending.merge ? short(pending.merge) : 'none'}</dd>` +
    `<dt>remote's ${esc(pending.target)}</dt><dd class="mono">${pending.remoteTip ? short(pending.remoteTip) : 'not read'}</dd>` +
    `<dt>outcome</dt><dd>${esc(outcome)}</dd>` +
    `<dt>what the remote said</dt><dd>${esc(pending.detail || '—')}</dd>` +
    `<dt>operation</dt><dd><span class="mono">${esc(op.id || '—')}</span> · ${esc(op.source || 'api')}` +
    ` · ${op.actor ? esc(op.actor) : 'actor not verified (the board endpoint authenticates nobody)'}</dd>` +
    `</dl>${history ? `<ul class="cmp-history">${history}</ul>` : ''}</details>` +
    `<div class="check-row"><span class="glyph">·</span><span class="name">settled by hand?</span>` +
    `<span class="state"><button id="sh-forget" class="linkish">Discard this record</button> ` +
    `— changes nothing in git; use it once you have decided what the unfinished merge means.` +
    `</span></div></div>`;
}

async function completeSheet(task, from) {
  // read before anything is drawn: the sheet describes the completion that
  // would run, and a sheet that guessed would be the bug this replaced
  let facts = null, factsError = '';
  try {
    const res = await fetch(`/api/task/completion?file=${encodeURIComponent(task.file)}` +
                            `&stage=${encodeURIComponent(from)}`);
    const data = await res.json();
    if (res.ok) facts = data; else factsError = data.error || 'it could not be read';
  } catch (e) { factsError = 'the board did not answer'; }
  const d = S.state.drive;
  const driving = d && d.task === task.file && ['starting', 'up'].includes(d.status);
  // A phase card stands for cards the Board does not draw, so the sheet
  // has to say what merging it does to them: the ones it merged go to
  // done/ with it and their workspaces go too. Counted the way the lane
  // counts them, from the runner's reading and the phase's own log.
  const swept = task.isPhase ? mergedIn(task).size : 0;
  // And what this board's acceptance gate says, before the button that
  // the server will refuse. One reading, so the sheet and the refusal
  // give one reason — and said here rather than only in the toast
  // afterwards, because "merge" is the click a person should not have to
  // make to find out.
  const gate = (S.state.gates || {})[task.file];
  const blocked = gate && gate.decision === 'blocked' ? gate : null;
  const words = facts ? completionWords(facts, task, driving, swept) : null;
  const pending = facts && facts.recovery;
  // a refusal the server will make anyway is said instead of a merge button:
  // a stacked card, a target that has gone, no PR to merge on origin
  const refusal = facts && facts.refusal
    ? `<div class="panel bad"><div class="phead"><span class="label">Merge &amp; clean up ` +
      `is refused here</span></div><div class="check-row fail"><span class="glyph">✕</span>` +
      `<span class="state">${esc(facts.refusal)}</span></div></div>` : '';
  const unfinished = pending ? unfinishedPanel(pending) : '';
  const wrap = $('#sheetwrap');
  wrap.innerHTML =
    `<div class="sheet">` +
    `<div class="stitle"><span>${esc(task.title)}</span>` +
    `<span class="mono">${task.number ? '#' + esc(task.number) + ' · ' : ''}→ done</span></div>` +
    (blocked
      ? `<div class="panel bad"><div class="phead"><span class="label">` +
        `Its ${esc(blocked.scope)} gate is unmet</span></div>` +
        `<div class="check-row fail"><span class="glyph">✕</span>` +
        `<span class="name">${esc(blocked.unmet.map(u => u.name).join(', '))}` +
        `</span><span class="state">${esc(blocked.summary)}</span></div>` +
        `<div class="check-row"><span class="glyph">·</span>` +
        `<span class="name">what happens</span><span class="state">Merge &amp; ` +
        `clean up is refused for this reason — the card, its branch and its ` +
        `worktree are left exactly as they are. The ⚖ chip has the remedy.` +
        `</span></div></div>`
      : '') +
    unfinished + refusal +
    (factsError
      ? `<p>Bench could not say beforehand how this card would be completed (${esc(factsError)}). ` +
        `The server still decides, and refuses rather than guesses.</p>` : '') +
    `<p>This task has ${task.pr ? 'a PR and ' : ''}a branch with work on it. What should happen?</p>` +
    `<div class="sbtns">` +
    `<button id="sh-keep">Keep it where it is<small>Nothing moves, nothing changes.</small></button>` +
    `<button id="sh-move">Just move the card<small>Only the ticket moves: no merge, no push, no ` +
    `cleanup. The branch${task.pr ? ', PR' : ''} and worktree stay as they are` +
    `${task.isPhase ? ", and so does every card in the phase" : ''} — ` +
    `and done/ here is not a record that the code was published.</small></button>` +
    (facts && facts.refusal ? '' :
      `<button id="sh-ship" class="shipit">${pending ? 'Retry publication &amp; clean up' : 'Merge &amp; clean up'}` +
      `<small>${words ? words.ship : 'Merge, publish, clean up and move the card, as this board is configured to.'}` +
      `${words && words.tickets ? ' ' + words.tickets : ''}</small></button>`) +
    `</div></div>`;
  wrap.classList.add('open');
  wrap.addEventListener('click', (e) => { if (e.target === wrap) closeSheet(); });
  $('#sh-keep').addEventListener('click', closeSheet);
  $('#sh-move').addEventListener('click', () => { closeSheet(); rawMove(task.file, from, 'done'); });
  const forget = $('#sh-forget');
  if (forget) forget.addEventListener('click', async () => {
    // armed: the first click says what it does, the second does it
    if (!forget.dataset.armed) { forget.dataset.armed = '1'; forget.textContent = 'Discard it — click again'; return; }
    closeSheet();
    const res = await fetch('/api/task/complete/forget', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file: task.file }),
    });
    const data = await res.json();
    toast(res.ok ? `${task.file}: record discarded — nothing in git changed` : (data.error || 'could not discard it'), !res.ok);
    await loadState();
  });
  const ship = $('#sh-ship');
  if (ship) ship.addEventListener('click', async () => {
    closeSheet();
    toast(`Completing ${task.file} — the card shows each step`);
    const res = await fetch('/api/task/complete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file: task.file, from }),
    });
    const data = await res.json();
    if (!res.ok) { toast(data.error || 'completion failed — the card stays', true); }
    else if (data.published === false && data.publication &&
             data.publication.state !== 'local-only') {
      /* merged, not delivered: say what already happened and what did not,
         and never call it done — the card is still in review/ */
      const p = data.publication;
      const next = (data.recovery && data.recovery.action) || 'Resolve it, then drag to done/ to retry.';
      toast(p.deliveredElsewhere
        ? `${task.file}: ${p.remote || 'the remote'}/${p.target} already holds its work through ` +
          `another merge; the merge made here (${String(p.merge).slice(0, 12)}) is not published ` +
          `— publication failed. Nothing was cleaned up; the card stays in review/. ${next}`
        : `${task.file} merged into ${p.target} at ${String(p.commit).slice(0, 12)}, but ` +
          `publication failed or is unconfirmed (${p.outcome || p.state}) — ` +
          `${p.remote || 'the remote'}/${p.target} does not hold it (${p.detail}). ` +
          `Nothing was cleaned up; the card stays in review/. ${next}`, true);
    } else {
      const went = (data.swept || []).length;
      const kept = (data.kept || []).length;
      /* `merged` says a commit landed, not that the card is accepted: a
         branch that moved past the gated commit leaves the card where it
         was, and the toast must not say done/ about it. */
      if (data.partial) {
        toast(`${task.file} partly completed — ${String(data.unmerged.merged).slice(0, 12)} ` +
              `merged, but ${data.unmerged.branch} has moved on and is not merged; ` +
              `the card stays in ${data.stage}/`, true);
      } else {
        const local = data.publication && data.publication.state === 'local-only';
        const notes = (data.cleanup && data.cleanup.notes) || [];
        const caught = data.baseCatchUp && !['synced', 'sync-beat', 'no-remote'].includes(data.baseCatchUp.state)
          ? ` · the local base was not caught up (${data.baseCatchUp.detail || data.baseCatchUp.state})` : '';
        if (data.reconciled) notes.push(...(caught ? [caught.slice(3)] : []));
        toast((data.reconciled
                ? `${task.file} was already delivered — the remote holds ` +
                  `${String((data.publication || {}).head || '').slice(0, 12)}; nothing was merged` +
                  (notes.length ? `; kept — ${notes.join('; ')}` : ', cleaned up')
                : data.merged ? `${task.file} merged${local ? ' locally (no remote — nothing published)' : ' and published'}` +
                (notes.length ? `; kept — ${notes.join('; ')}` : ', cleaned up') +
                (went ? ` — ${went} card${went === 1 ? '' : 's'} went to done/ with it` : '')
              : `${task.file} moved to done`) +
              (kept ? ` · ${kept} card${kept === 1 ? '' : 's'} moved on since the phase ` +
                      `merged ${kept === 1 ? 'it' : 'them'} and stay${kept === 1 ? 's' : ''} put` : ''),
              kept > 0 || notes.length > 0);
      }
    }
    await loadState();
  });
}

/* Joining a phase is a choice between a few named options, so it is put in
   front of you the way finishing a card with work on it is — a short list
   of phases, each saying what it already holds, rather than a guess. What
   it writes is one line at the end of that phase's `## Cards`; the card
   itself does not move, and says nothing about the phase it joined. */
function phaseSheet(task) {
  const phases = joinablePhases(task);
  if (!phases.length) { toast('no phase is waiting in to-do/', true); return; }
  const wrap = $('#sheetwrap');
  wrap.innerHTML =
    `<div class="sheet">` +
    `<div class="stitle"><span>${esc(task.title)}</span>` +
    `<span class="mono">${task.number ? '#' + esc(task.number) + ' · ' : ''}⟶ phase</span></div>` +
    `<p>Which phase runs this card? It goes at the end of that phase's list. ` +
    `The card stays in ${esc(task.stage)}/ — joining a phase is not a commitment to start it.</p>` +
    `<div class="sbtns">` +
    phases.map((p, index) => {
      const held = (p.cards || []).length;
      return `<button data-pick="${index}">${esc(p.title)}` +
        `<small>${held ? `holds ${held} card${held === 1 ? '' : 's'} — this one runs after them`
                       : 'empty so far — this one would be its first card'}</small></button>`;
    }).join('') +
    `<button id="sh-nophase">Not now<small>Nothing is written.</small></button>` +
    `</div></div>`;
  wrap.classList.add('open');
  wrap.addEventListener('click', (e) => { if (e.target === wrap) closeSheet(); });
  $('#sh-nophase').addEventListener('click', closeSheet);
  wrap.querySelectorAll('[data-pick]').forEach(btn =>
    btn.addEventListener('click', () => {
      closeSheet();
      addToPhase(task, phases[+btn.dataset.pick]);
    }));
}

async function addToPhase(task, phase) {
  const res = await fetch('/api/phase/add', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: task.file, stage: task.stage, phase: phase.file }),
  });
  const data = await res.json();
  toast(res.ok ? `${task.file} added to ${data.phaseName}`
               : (data.error || 'that card did not join'), !res.ok);
  await loadState();
  return res.ok;
}

function showDetail(task) {
  const changed = !S.selected || S.selected.file !== task.file;
  S.selected = task;
  S.cardLink = null;
  renderCards();
  if (changed) $('#drawerbody').scrollTop = 0;
  // opening a card is a destination: its address (task 134)
  syncRoute();
}

/* The runner's word for a member, in the vocabulary the rest of the board
   uses. `pending` is the phase simply not having reached it, which the
   stage beside it already says — so it says nothing. */
const MEMBER_STATE = { merged: 'merged in', running: 'working',
  waiting: 'checking', ready: 'ready to merge', halt: 'stopped here' };

/* A phase card answers "where is this up to" on its own face: its members
   in run order, each with the stage it is in, so the question costs no
   hunt across five columns. The runner's reading (merged in, working,
   stopped here) is added only while a phase is in flight, because only
   then is there one — and only it can tell a card that has run from one
   the phase has not reached. Each row opens that card. */
/* The card's execution plan in the drawer: where its work starts, what
   its PR targets, and what said so. Drawn only for a card that has
   something to say — a card on the application default has nothing here,
   which is also what keeps the drawer unchanged for every board that has
   never named a base. Drift is drawn in `--alarm` and says the whole
   thing, because a launch is going to refuse until it is settled. */
function planBlock(t) {
  const p = (S.state.plans || {})[t.file];
  if (!p) return '';
  const drift = (p.drift || []).length;
  const rows = drift
    ? p.drift.map(d => ['does not resolve', d, ''])
    : [['starts from', p.startBranch + (p.startCommit ? ` · ${p.startCommit.slice(0, 9)}` : ''),
        p.source],
       ['PR targets', p.prBase, p.prSource]];
  return `<div class="pmembers"><div class="phead">Execution plan` +
    `<span class="mono">${p.recorded ? (p.provenance === 'adopted'
      ? 'recorded with the run · adopted' : 'recorded with the run')
      : 'not recorded yet'}</span></div>` +
    rows.map(([k, v, why]) => `<div class="prow flat${drift ? ' bad' : ''}">` +
      `<span class="pstage">${esc(k)}</span>` +
      `<span class="ptitle mono">${esc(v)}</span>` +
      `<span class="pstage">${esc(why)}</span></div>`).join('') +
    `</div>`;
}

function phaseMembers(t) {
  if (!t.isPhase) return '';
  if (!(t.members || []).length) {
    return `<div class="pmembers"><div class="phead">Cards</div>` +
      `<div class="pempty">This phase lists no cards yet — a <code>## Cards</code> ` +
      `section, one task number per line, in the order they run.</div></div>`;
  }
  const snap = (S.state.phases || {})[t.file];
  const read = {};
  for (const m of ((snap && snap.members) || [])) read[m.file] = m;
  const rows = t.members.map((m, i) => {
    const seen = read[m.file];
    const note = seen ? MEMBER_STATE[seen.state] || '' : '';
    const tint = seen && seen.state === 'halt' ? 'var(--alarm)'
      : seen && ['running', 'waiting'].includes(seen.state) ? 'var(--accent)'
      : seen && seen.state === 'merged' ? 'var(--calm)' : '';
    return `<button class="prow" data-member="${esc(m.file)}">` +
      `<span class="pn mono">${i + 1}</span>` +
      `<span class="pref mono">#${esc(m.number || '')}</span>` +
      `<span class="ptitle">${esc(m.title)}</span>` +
      `<span class="pstage mono">${esc(m.stage)}</span>` +
      (note ? `<span class="pstate mono"${tint ? ` style="color:${tint}"` : ''}` +
              `${seen.why ? ` title="${esc(seen.why)}"` : ''}>${esc(note)}</span>` : '') +
      `</button>`;
  }).join('');
  const halt = snap && snap.halted
    ? `<div class="pempty" style="color:var(--alarm)">halted` +
      `${snap.haltedAt ? ` at #${esc(snap.haltedAt)}` : ''} — ` +
      `${esc(snap.haltedWhy || snap.halted)}</div>` : '';
  return `<div class="pmembers"><div class="phead">Cards` +
    `<span class="mono">${t.members.length} in run order</span></div>` +
    rows + halt + `</div>`;
}

export {
  actLabel, acts, addToPhase, ARM_MS, armAction, askCopilot, BOARD_ACT, canArchive,
  cardFor, cardSpec, cardTitle, chipOnFace, closeSheet, completeSheet, completionWords,
  fetchVerifyLog, fireAction, fireAgent, focusCard,
  forgetActsOfVanishedCards, joinablePhases, keyedCard, lockAction,
  MEMBER_STATE, move, openPR, parkDrive, phaseMembers, phaseSheet, planBlock,
  rawMove, replan, REQ_ITEM, requiredItemRow, requiredSheet, requiredTimer,
  rerunCheck, reuseDetail, REVIEW_LABELS, REVIEW_TARGETS, reviewChip,
  reviewPanel, reviewSheet, runCommand, showDetail, startDrive,
  unfinishedPanel, verifyDetail, verifySheet, wireAction
};
