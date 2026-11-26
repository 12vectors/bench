/* Agent-reported progress, kept apart from what bench verified. */
import { scheduleRender } from '../app/data.js';
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { esc } from '../lib/dom.js';
import { ago, fmtClock, fmtShort } from '../lib/time.js';
import { gateState } from './gates.js';
import { fold } from './sessions.js';
import { verifyRows } from './verification.js';

/* ── agent-reported progress (task 73) ─────────────────────────────────

   What a run *said* about its work, kept apart from three other facts it
   is easy to confuse with: when its last tool event arrived, whether its
   process is still running, and what bench itself verified. Each is drawn
   with its own clock and its own words; silence is never filled in, and
   no percentage is computed from anything. */

// A progress update older than this, while tool events keep arriving, is
// "no recent progress update" — said as exactly that, never as "stuck".
const PROGRESS_QUIET = 600;

/* One run's progress: the compact reading the state payload carries for
   the card's most recent run, or the whole timeline once fetched. */
function progressOf(agentId, file) {
  if (!agentId) return null;
  const full = S.progressFull[agentId];
  if (full && full.data) return full.data;
  const onCard = file && (S.state?.progress || {})[file];
  return onCard && onCard.agent === agentId ? onCard : null;
}

async function fetchProgress(agentId) {
  if (!agentId) return;
  const cached = S.progressFull[agentId];
  if (cached && (cached.fetching || Date.now() - cached.at < 8000)) return;
  S.progressFull[agentId] = { ...(cached || {}), fetching: true };
  try {
    const res = await fetch('/api/progress?agent=' + encodeURIComponent(agentId));
    const data = res.ok ? (await res.json()).progress : null;
    S.progressFull[agentId] = { at: Date.now(), data };
    if (S.view === 'focus' || S.view === 'flight') scheduleRender();
  } catch {
    S.progressFull[agentId] = { at: Date.now(), data: cached ? cached.data : null };
  }
}

// The process state in words, from the board's own launch record — a
// different fact from anything the run reported.
const PROCESS_WORDS = { running: 'running', done: 'finished', failed: 'failed',
                        stopped: 'held', declined: 'declined' };
const ENDED_WORDS = { finished: 'finished', failed: 'failed', declined: 'declined',
                      held: 'was held', unobserved: 'ended unobserved — the board restarted' };

function processWord(agent, p) {
  if (agent && agent.status && !agent.replayed) return PROCESS_WORDS[agent.status] || agent.status;
  if (p && !p.active) return ENDED_WORDS[p.outcome] || 'ended';
  return 'not known to this board';
}

/* The one honest sentence about this run's reporting, from what is known
   and nothing else. `tool` is the time of the newest tool event. */
function progressSentence(p, agent, tool) {
  const now = Date.now() / 1000;
  if (!p) return { text: 'No progress was recorded for this run — it predates progress reporting, or another board launched it.' };
  if ((p.channel || {}).state !== 'available') {
    return { text: `Reporting unavailable: ${p.channel?.why || 'this run was given no progress channel'}. Its tool activity is all the board can show.` };
  }
  if (!p.active) {
    return { text: `The run ${ENDED_WORDS[p.outcome] || 'ended'} ${p.closed ? ago(p.closed) + ' ago' : ''}. ` +
      (p.current ? 'Its last update is history now, not what it is doing.' : 'It sent no progress update.') };
  }
  const toolRecent = tool && now - tool < PROGRESS_QUIET;
  if (!p.current) {
    return { text: toolRecent ? 'No progress update yet; tool activity continues.'
                              : 'No progress update yet.' };
  }
  if (p.current.blocked) return { text: 'The run reports that it is blocked.', warn: true };
  if (now - (p.lastReport || 0) > PROGRESS_QUIET && toolRecent && tool > (p.lastReport || 0)) {
    return { text: `No recent progress update (last ${ago(p.lastReport)} ago); tool activity continues.` };
  }
  return null;
}

/* The face of a card an agent is on: what the run last *said*, labelled as
   said, with its own age. Only while the run is alive and only for this
   run — a new launch never wears its predecessor's "doing now". */
function progressWell(task, agent) {
  if (!agent || agent.status !== 'running') return '';
  const p = (S.state?.progress || {})[task.file];
  if (!p || p.agent !== agent.id || !p.active || (p.channel || {}).state !== 'available') return '';
  const c = p.current;
  if (!c) {
    return `<div class="pwell quiet" aria-label="Progress">No progress update yet</div>`;
  }
  const said = c.doing || c.narrative || (c.remaining || [])[0] || '';
  const label = `Agent-reported progress, ${ago(p.lastReport)} ago`;
  return `<div class="pwell" aria-label="${esc(label)}" title="${esc(label)} — what the run said, not a verified result">` +
    `<div class="ptop"><span class="ptrust">agent-reported · ${esc(ago(p.lastReport))} ago</span>` +
    (c.stage ? `<span class="pstage">${esc(c.stage)}</span>` : '') + `</div>` +
    (said ? `<div>${esc(said.length > 160 ? said.slice(0, 160) + '…' : said)}</div>` : '') +
    (c.blocked ? `<div class="pblock">Blocked: ${esc(c.blocked.length > 160 ? c.blocked.slice(0, 160) + '…' : c.blocked)}</div>` : '') +
    `</div>`;
}

/* A reference the run attached, as the board can actually read it: a
   commit by its sha, or bench's own evidence record as it reads *now* —
   check, scope, commit and freshness from the runner, never from prose. */
function progressRef(ref) {
  if (ref.kind === 'commit') return `<span>commit ${esc(ref.id.slice(0, 12))} <span class="src">(named by the run)</span></span>`;
  if (!ref.found) return `<span class="bad">evidence ${esc(ref.id)} — not found on this board</span>`;
  const cls = ref.outcome === 'passed' ? 'ok' : (['failed', 'error', 'stale', 'prepare-failed', 'denied', 'cancelled'].includes(ref.outcome) ? 'bad' : '');
  const bits = [`${esc(ref.check)}${ref.scope ? ' · scope ' + esc(ref.scope) : ' · full run'}`,
    `<b class="${cls}">${esc(ref.outcome)}</b>`,
    ref.outcome !== ref.recorded ? `recorded ${esc(ref.recorded)}, now ${esc(ref.reason || ref.outcome)}` : '',
    ref.commit ? `at ${esc(ref.commit)}${ref.dirty ? ' + uncommitted' : ''}` : '',
    'recorded by bench'].filter(Boolean);
  return `<span>evidence ${esc(ref.id)}: ${bits.join(' · ')}</span>`;
}

/* The Focus view's progress section: done / doing now / remaining /
   blocked, the narrative, the milestone checklist, three separate clocks,
   the channel's health and the timeline — every claim labelled as the
   run's, every absence said as an absence. */
function progressSection(agent, meta, task) {
  if (!agent) return '';
  fetchProgress(agent.id);
  const p = progressOf(agent.id, task && task.file);
  const events = (meta && S.events[meta.id]) || [];
  const tool = (meta && meta.last) || (events.length ? events[events.length - 1].ts : null);
  const c = p && p.current;
  const ended = p && !p.active;
  const row = (dt, value, cls) =>
    `<dt>${dt}</dt><dd class="${value ? (cls || '') : 'unknown'}">${value || 'not reported'}</dd>`;
  const miles = p && (p.milestones || []).length
    ? `<ul class="miles">${p.milestones.map(m => `<li>${esc(m.text)}</li>`).join('')}</ul>` +
      (p.milestonesOmitted ? `<span class="unknown">and ${Number(p.milestonesOmitted)} later milestone${p.milestonesOmitted === 1 ? '' : 's'} not listed</span>` : '')
    : '';
  const honest = progressSentence(p, agent, tool);
  const clock =
    `<div class="pclock" role="group" aria-label="Three separate clocks">` +
    `<span><b>Last progress update</b> ${p && p.lastReport ? esc(ago(p.lastReport)) + ' ago · ' + fmtShort(p.lastReport) : 'none'}</span>` +
    `<span><b>Last tool activity</b> ${tool ? esc(ago(tool)) + ' ago · ' + fmtShort(tool) : 'none'}</span>` +
    `<span><b>Process</b> ${esc(processWord(agent, p))}</span></div>`;
  let body = '';
  if (p && (p.channel || {}).state === 'available') {
    body = `<dl>` +
      row('Stage', c && esc(c.stage)) +
      `<dt>Done</dt><dd class="${miles ? '' : 'unknown'}">${miles || 'none reported'}</dd>` +
      row(ended ? 'Last said' : 'Doing now', c && esc(c.doing), ended ? 'past' : '') +
      row('Remaining', c && (c.remaining || []).map(esc).join('\n')) +
      `<dt>Blocked</dt><dd class="${c && c.blocked ? 'blocked' : 'unknown'}">${c && c.blocked ? esc(c.blocked) : 'none reported'}</dd>` +
      `</dl>` +
      (c && c.narrative ? `<p class="pnarr">${esc(c.narrative)}</p>` : '');
    const refs = c && (c.refs || []);
    if (refs && refs.length) {
      body += `<div class="prefs" aria-label="References the run attached">${refs.map(progressRef).join('')}</div>`;
    }
  }
  const counts = (p && p.counts) || {};
  const health = p
    ? `Reporting channel: ${esc((p.channel || {}).state || 'unknown')}` +
      ` · ${counts.accepted || 0} update${counts.accepted === 1 ? '' : 's'} received` +
      (counts.coalesced ? ` · ${counts.coalesced} coalesced` : '') +
      (counts.duplicates ? ` · ${counts.duplicates} duplicate${counts.duplicates === 1 ? '' : 's'} ignored` : '') +
      (counts.rejected ? ` · ${counts.rejected} refused (last: ${esc((p.lastRejection || {}).why || '')})` : '') +
      // requests that named this run without its token (WP05)
      Object.entries(p.unproven || {}).map(([where, n]) =>
        ` · ${esc(String(n))} unproven claim${n === 1 ? '' : 's'} on /api/${esc(where)}`).join('')
    : '';
  const history = (p && p.history) || [];
  const verifies = task ? (S.state.boardEvents || []).filter(be =>
    be.kind === 'verify' && be.file === task.file && (!p || be.ts >= (p.opened || 0))) : [];
  const timeline = history.map(h => ({ ts: h.at, html:
      `<span class="src">agent-reported</span><span>${esc([h.stage, h.doing || h.narrative].filter(Boolean).join(' — ') || (h.done || []).join('; '))}` +
      (h.blocked ? ` <b style="color:var(--alarm)">blocked: ${esc(h.blocked)}</b>` : '') +
      (h.coalesced ? ` <span class="src">(${h.coalesced + 1} updates coalesced)</span>` : '') + `</span>` }))
    .concat(verifies.map(be => ({ ts: be.ts, html: `<span class="src">bench</span><span>${esc(be.summary)}</span>` })))
    .sort((a, b) => b.ts - a.ts)
    .map(item => `<div class="pti"><time>${fmtClock(item.ts)}</time>${item.html}</div>`).join('');
  const tkey = `ptl:${agent.id}`;
  const fold = timeline
    ? `<details class="fold" data-key="${esc(tkey)}"${S.openFolds.has(tkey) ? ' open' : ''}>` +
      `<summary>Progress timeline (${history.length} update${history.length === 1 ? '' : 's'}${verifies.length ? `, ${verifies.length} verification event${verifies.length === 1 ? '' : 's'}` : ''})</summary>` +
      `<div class="ptl">${timeline}</div></details>` : '';
  return `<section class="prog" aria-label="Progress">` +
    `<div class="prog-head"><span class="label">Progress</span>` +
    `<span class="ptrust" title="What the run says about its own work. Not verification evidence.">agent-reported</span>` +
    (c ? `<span class="pclock">update ${esc(ago(p.lastReport))} ago</span>` : '') + `</div>` +
    (honest ? `<div class="phonest${honest.warn ? ' warn' : ''}" role="status">${esc(honest.text)}</div>` : '') +
    body + clock +
    (health ? `<div class="pclock">${health}</div>` : '') + fold + `</section>`;
}

async function fetchDiff(agentId) {
  const cached = S.diffCache[agentId];
  if (cached && (cached.fetching || Date.now() - cached.at < 8000)) return;
  S.diffCache[agentId] = { ...(cached || {}), fetching: true };
  try {
    const res = await fetch('/api/diff?agent=' + encodeURIComponent(agentId));
    const data = await res.json();
    S.diffCache[agentId] = { at: Date.now(), files: data.files || [] };
    if (S.view === 'focus') scheduleRender();
  } catch {
    S.diffCache[agentId] = { at: Date.now(), files: cached ? cached.files : [] };
  }
}

/* What bench's own runner found for this card, and what it has not been
   asked to find. Fetched like the manifest and read back off disk: the
   panel is a view of stored records, so drawing it runs nothing — which
   is the rule a detail panel for test results has to follow. */
function verificationPanel(task, agent) {
  const head = (right) =>
    `<div class="panel"><div class="phead"><span class="label">Verification</span>` +
    `<span class="r">${right}</span></div>`;
  if (!task) {
    return head('—') +
      `<div class="check-row none"><span class="glyph">—</span>` +
      `<span class="name">no card attached</span>` +
      `<span class="state">bench verifies a card's work</span></div></div>`;
  }
  const cached = S.evidence[task.file];
  if (!cached || !cached.rollup) {
    return head('reading') +
      `<div class="check-row none"><span class="glyph">·</span>` +
      `<span class="name">reading the evidence</span>` +
      `<span class="state">loading</span></div></div>`;
  }
  const rollup = cached.rollup;
  const rows = rollup.rows || [];
  const live = rows.some(r => r.live);
  const unverified = agent && agent.status !== 'running'
    && rows.length && !rows.some(r => r.outcome === 'passed');
  return head(esc(live ? 'running now' : (rollup.location === 'local'
                                          ? 'local to this board' : 'synchronized'))) +
    verifyRows(rollup, { links: false }) +
    (unverified
      ? `<div class="check-row none"><span class="glyph">·</span>` +
        `<span class="name">the run has finished</span>` +
        `<span class="state">nothing here passed — a finished agent is not a ` +
        `verified card</span></div>`
      : '') +
    `<p class="why" style="margin:6px 0 0;color:var(--dim);font-size:11.5px">` +
    `Each row is a process bench launched and waited on. Open the card's ` +
    `◫ chip for the logs, the commits they ran against and a rerun.</p>` +
    `</div>`;
}

/* Code produced, work verified, work accepted — three states an exit code
   cannot tell apart, drawn apart here. The panel reads the gate the state
   payload already carries, so it costs nothing and starts nothing; the
   card's ⚖ chip is where the whole reading and the remedies live. */
function focusGatePanel(task, agent) {
  const head = (right) =>
    `<div class="panel"><div class="phead"><span class="label">Gate</span>` +
    `<span class="r">${right}</span></div>`;
  if (!task) {
    return head('—') +
      `<div class="check-row none"><span class="glyph">—</span>` +
      `<span class="name">no card attached</span>` +
      `<span class="state">a gate is about a card's own merge</span>` +
      `</div></div>`;
  }
  const g = (S.state.gates || {})[task.file];
  if (!g) {
    return head('ungated') +
      `<div class="check-row none"><span class="glyph">—</span>` +
      `<span class="name">no gate configured</span>` +
      `<span class="state">this board requires nothing before a merge, so ` +
      `nothing here is proved — not a pass</span></div></div>`;
  }
  const [cls, glyph, word] = gateState(g.decision);
  const finished = agent && agent.status && agent.status !== 'running';
  const row = (name, value, kls) =>
    `<div class="check-row ${kls || ''}"><span class="glyph">·</span>` +
    `<span class="name">${esc(name)}</span>` +
    `<span class="state">${esc(value)}</span></div>`;
  return head(esc(`${g.scope} · local to this board`)) +
    `<div class="check-row ${cls === 'ok' ? 'pass' : cls === 'bad' ? 'fail' : ''}">` +
    `<span class="glyph">${glyph}</span>` +
    `<span class="name">${esc(word)}</span>` +
    `<span class="state">${esc(g.summary)}</span></div>` +
    row('code produced',
        finished ? 'the run has ended and its commits are on the branch'
                 : 'the run is still working') +
    row('verified',
        g.decision === 'allowed'
          ? `${g.met} of ${g.required} requirements met against ` +
            `${String(g.head || '').slice(0, 12)}`
          : g.decision === 'overridden'
            ? 'no — an authorised person proceeded over an unmet gate'
            : `no — ${g.unmet.length} unmet`,
        g.decision === 'allowed' ? 'pass' : 'fail') +
    (g.unmet || []).map(u =>
      `<div class="check-row fail" style="padding-left:18px">` +
      `<span class="glyph">✕</span><span class="name">${esc(u.name)}</span>` +
      `<span class="state">${esc(u.reason)}</span></div>`).join('') +
    `<p class="why" style="margin:6px 0 0;color:var(--dim);font-size:11.5px">` +
    `A finished agent is not a verified card, and a verified card is not an ` +
    `accepted one. The card's ⚖ chip has the whole reading and what to do ` +
    `about each unmet requirement.</p></div>`;
}

async function fetchEvidence(file, stage) {
  const cached = S.evidence[file];
  if (cached && (cached.fetching || Date.now() - cached.at < 6000)) return;
  S.evidence[file] = { ...(cached || {}), fetching: true };
  try {
    const res = await fetch('/api/evidence?file=' + encodeURIComponent(file) +
                            '&stage=' + encodeURIComponent(stage || ''));
    const data = res.ok ? await res.json() : {};
    S.evidence[file] = { at: Date.now(), rollup: data.rollup || null,
                         evidence: data.evidence || [] };
    if (S.view === 'focus') scheduleRender();
  } catch {
    S.evidence[file] = { at: Date.now(),
                         rollup: cached ? cached.rollup : null,
                         evidence: cached ? cached.evidence : [] };
  }
}

/* The readiness reading. A GET, so this starts no process, pins nothing
   and spends nothing — but it does resolve every intent and hash every
   approved input, so it is held for a minute rather than taken on every
   SSE nudge. A reading older than that is still shown while a fresh one
   is on the way: the panel's own words are about when it was taken. */
async function fetchReadiness() {
  const cached = S.doctor;
  if (cached && (cached.fetching || Date.now() - cached.at < 60000)) return;
  S.doctor = { ...(cached || { at: 0 }), fetching: true };
  try {
    const res = await fetch('/api/doctor');
    const data = res.ok ? await res.json() : {};
    if (data.doctor) S.doctor = { at: Date.now(), report: data.doctor };
    else S.doctor = { at: Date.now(), report: cached ? cached.report : null };
    scheduleRender();
  } catch {
    S.doctor = { at: Date.now(), report: cached ? cached.report : null };
  }
}

/* The manifest one run was frozen with. Fetched rather than carried in the
   state payload, because it holds a capability table per run and the
   payload rides every SSE nudge — and read back off disk rather than
   resolved again, which is the whole guarantee: a profile edited mid-run
   retargets the next launch and never this record. */
async function fetchManifest(agentId) {
  const cached = S.manifests[agentId];
  if (cached && (cached.fetching || Date.now() - cached.at < 8000)) return;
  S.manifests[agentId] = { ...(cached || {}), fetching: true };
  try {
    const res = await fetch('/api/manifest?agent=' + encodeURIComponent(agentId));
    const data = res.ok ? await res.json() : {};
    S.manifests[agentId] = { at: Date.now(), manifest: data.manifest || null,
                             freshness: data.freshness || null,
                             // the handbook as it reads today, kept apart
                             // from what this run was given (task 203)
                             handbookNow: data.handbookNow || null };
    if (S.view === 'focus') scheduleRender();
  } catch {
    S.manifests[agentId] = { at: Date.now(),
                             manifest: cached ? cached.manifest : null,
                             freshness: cached ? cached.freshness : null,
                             handbookNow: cached ? cached.handbookNow : null };
  }
}

export {
  ENDED_WORDS, fetchDiff, fetchEvidence, fetchManifest, fetchProgress,
  fetchReadiness, focusGatePanel, PROCESS_WORDS, processWord, PROGRESS_QUIET,
  progressOf, progressRef, progressSection, progressSentence, progressWell,
  verificationPanel
};
