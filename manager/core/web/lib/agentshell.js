/* The shared conversation panel's common presentation (task 206), and the
   two Brain adapters that need nothing but it.

   The panel (lib/panel.js) owns the shell; this module owns the pieces
   every purpose draws the same way, so one purpose cannot word a state
   differently from another:

   - **What you can do here** — the purpose's follow-up, cancellation and
     reporting, each supported or not, from lib/agentpurposes.js. A
     purpose without a composer says so in words, with the supported next
     step, rather than leaving an empty space that looks like one is coming.
   - **Progress** — done / now / remaining / blocked as the run or bench
     reported them, apart from the last tool activity (activity, not
     progress) and the observed process. Nothing reported is said as that.
   - **Transcript** — the public lines of a conversation, every word esc()'d.
   - **The workspace** — the link to the specialized workspace a purpose's
     approval, review and Apply still happen in.

   The adapters here — a Brain seed or evaluation draft (`brainjob`) and
   the reconciliation queue (`reconcile`) — only read: they fetch the
   public draft and queue readings the Brain's own panels already read, and
   draw them. Every action stays in the workspace, under its own approval
   and revision guards; nothing here posts anything.

   Imports lib/ only. */
import { fetch } from './api.js';
import { esc } from './dom.js';
import { purpose } from './agentpurposes.js';
import { activeSlot, panelOpen, registerAgent, renderPanel } from './panel.js';

/* ── shared pieces ──────────────────────────────────────────────────── */

function limitsHtml(p) {
  if (!p) return '';
  const row = (k, v) => `<dt>${esc(k)}</dt><dd><b>${v.supported ? 'Supported' : 'Not supported'}.</b> ${esc(v.how)}</dd>`;
  return `<details class="ap-limits"${p.followUp.supported ? '' : ' open'}><summary>What you can do here</summary>` +
    `<dl class="ap-facts">${row('Follow-up', p.followUp)}${row('Cancel', p.cancel)}${row('Live reporting', p.reporting)}</dl>` +
    `<p class="ap-note">Typing here is never a command: no conversation starts or stops work, runs a check, ` +
    `or changes a merge rule, a budget or an agreement.</p></details>`;
}

/* A purpose that takes no follow-up says so where a composer would be. */
function noComposerHtml(p, next) {
  return `<div class="ap-nofollow" role="note"><p><b>No follow-up here.</b> ${esc(p ? p.followUp.how : '')}</p>` +
    (next ? `<p>${next}</p>` : '') + `</div>`;
}

function listHtml(items) {
  return (items && items.length) ? `<ul>${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '<span class="ap-unknown">none reported</span>';
}

/* done / now / remaining / blocked as reported, then the tool activity
   and the process as separate facts. `src` names whose words they are. */
function progressBlockHtml(pr, src) {
  const p = pr || {};
  const reported = p.doing || p.blocked || (p.done || []).length || (p.remaining || []).length;
  const tool = p.toolActivity;
  const toolLine = tool ? `<p class="ap-note">Last tool activity: ${tool.last
    ? esc(typeof tool.last === 'object' ? (tool.last.summary || '') : new Date(tool.last * 1000).toLocaleTimeString())
    : 'none yet'}${tool.inFlight ? ` (${esc(String(tool.inFlight))})` : ''} — activity, not progress.</p>` : '';
  const proc = p.process ? `<p class="ap-note">Process: ${esc(p.process.status || 'unknown')} — observed by bench.</p>` : '';
  if (!reported) {
    return `<section class="ap-progress" aria-label="Progress"><h3>Progress</h3>` +
      `<p class="ap-note">No progress reported${src ? ` by ${esc(src)}` : ''}.</p>${toolLine}${proc}</section>`;
  }
  return `<section class="ap-progress" aria-label="Progress"><h3>Progress <span class="ap-src">${esc(src || 'reported')}</span></h3>` +
    `<dl class="ap-facts"><dt>Done</dt><dd>${listHtml(p.done)}</dd>` +
    `<dt>Now</dt><dd>${p.doing ? esc(p.doing) : '<span class="ap-unknown">not reported</span>'}</dd>` +
    `<dt>Remaining</dt><dd>${listHtml(p.remaining)}</dd>` +
    `<dt>Blocked on</dt><dd${p.blocked ? ' class="ap-block"' : ''}>${p.blocked ? esc(p.blocked) : 'nothing reported'}</dd></dl>` +
    toolLine + proc + `</section>`;
}

const WHO = { you: 'You', agent: 'Agent', board: 'Board' };
function transcriptHtml(lines, agentLabel) {
  const rows = (lines || []).map(c => `<li class="ap-line ap-${esc(c.who || 'board')}">` +
    `<span class="ap-at mono">${esc(c.at || '')}</span> <b>${esc(c.who === 'agent' ? (agentLabel || 'Agent') : WHO[c.who] || 'Board')}</b>` +
    `${c.kind ? ` · ${esc(c.kind)}` : ''}: ${esc(c.text || '')}` +
    (c.questions ? `<ul>${c.questions.map(q => `<li>${esc(typeof q === 'string' ? q : q.question || '')}</li>`).join('')}</ul>` : '') +
    `</li>`);
  return rows.length ? `<section class="ap-transcript" aria-label="Conversation"><h3>Conversation</h3><ol>${rows.join('')}</ol></section>`
    : `<p class="ap-note">No conversation yet.</p>`;
}

function workspaceHtml(p, href) {
  if (!p || !p.workspace) return '';
  return `<div class="ap-workspace"><a class="ap-wsbtn" href="${esc(href || p.workspace.href)}" data-route>${esc(p.workspace.label)}</a>` +
    `<p class="ap-note">${esc(p.workspace.why)}</p></div>`;
}

/* ── a Brain seed or evaluation draft (`brainjob`) ──────────────────── */

const JOB_WORDS = {
  running: 'A round is running — bench is watching its process',
  questions: 'The round ended with questions for you',
  proposed: 'The round ended with a draft to review',
  ready: 'Waiting for you to continue', applying: 'Applying the reviewed batch',
  applied: 'Ended: applied', failed: 'Ended: failed', cancelled: 'Ended: cancelled',
  discarded: 'Ended: discarded', expired: 'Ended: expired — its private text was removed',
};
const jobs = {};

async function readJob(id) {
  const j = jobs[id] = jobs[id] || {};
  if (j.reading) return;
  j.reading = true;
  try {
    const res = await fetch(`/api/brain/drafts?id=${encodeURIComponent(id)}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.draft) { j.data = data.draft; j.error = null; }
    else j.error = data.error || `the board answered ${res.status}`;
  } catch (e) { j.error = 'the board could not be asked'; }
  j.reading = false;
  if (panelOpen()) renderPanel('live');
  clearTimeout(j.poll);
  const s = activeSlot();
  // a running round is read again while its conversation is shown; nothing else
  if (j.data && j.data.status === 'running' && s && s.purpose === 'brainjob' && s.target.draft === id) {
    j.poll = setTimeout(() => readJob(id), 2000);
  }
}

function jobPurpose(t, d) {
  const mode = (d && d.mode) || t.mode;
  return purpose(mode === 'evaluate' ? 'brain-evaluate' : 'brain-seed');
}

function brainJobBody(s) {
  const t = s.target;
  const j = jobs[t.draft] || {};
  const d = j.data;
  const p = jobPurpose(t, d);
  if (!d) {
    return `<p class="ap-note" aria-busy="true">${j.error ? `Not read: ${esc(j.error)}` : 'Reading the draft…'}</p>` + workspaceHtml(p);
  }
  const ev = d.evaluate || {};
  const sd = d.seed || {};
  const summary = d.mode === 'evaluate'
    ? `<p class="ap-note">Evaluating ${esc(String((ev.entries || d.entries || []).length || ''))} selected entr${(ev.entries || d.entries || []).length === 1 ? 'y' : 'ies'}. ` +
      `Proposals are applied only as the batch you review.</p>`
    : `<p class="ap-note">${esc(sd.applies || 'Sections reach the Brain only through the reviewed, all-or-none Apply.')} Private to this board until applied.</p>`;
  const qs = d.status === 'questions' && d.questions && d.questions.length
    ? `<section class="ap-questions" aria-label="Questions waiting"><h3>Waiting for your answers</h3><ol>` +
      d.questions.map(q => `<li>${esc(q.question || '')}${q.why ? `<br><span class="ap-note">${esc(q.why)}</span>` : ''}</li>`).join('') +
      `</ol><p class="ap-note">Answer them in the workspace below: an answer continues the draft under its frozen approval.</p></section>` : '';
  return `<p role="status"><b>${esc(JOB_WORDS[d.status] || d.status)}</b> · round ${esc(String(d.round))} of ${esc(String(d.maxRounds))} ` +
    `<span class="ap-src">observed by bench</span></p>` +
    (d.error && d.error.why ? `<p class="ap-block" role="alert">${esc(d.error.why)}</p>` : '') +
    summary + transcriptHtml(d.conversation, 'Brain Agent') + progressBlockHtml(d.progress, 'bench') + qs +
    workspaceHtml(p, d.mode === 'evaluate' ? '/brain' : '/brain/seed') + limitsHtml(p) +
    `<p class="ap-note">Closing the panel stops nothing; the draft is kept on this board.</p>`;
}

/* ── the reconciliation queue, observed (`reconcile`) ───────────────── */

const queue = { data: null, error: null, reading: false };

async function readQueue() {
  if (queue.reading) return;
  queue.reading = true;
  try {
    const res = await fetch('/api/brain/reconcile');
    const data = await res.json().catch(() => ({}));
    if (res.ok) { queue.data = data.reconcile; queue.error = null; } else queue.error = data.error || `the board answered ${res.status}`;
  } catch (e) { queue.error = 'the board could not be asked'; }
  queue.reading = false;
  if (panelOpen()) renderPanel('live');
  clearTimeout(queue.poll);
  const s = activeSlot();
  const on = queue.data && queue.data.effective && queue.data.effective.on;
  if (on && s && s.purpose === 'reconcile') queue.poll = setTimeout(readQueue, 4000);
}

const ACTIVE = ['queued', 'running', 'waiting', 'review', 'failed', 'blocked', 'stale'];

function reconcileBody() {
  const p = purpose('brain-reconcile');
  const d = queue.data;
  if (!d) return `<p class="ap-note" aria-busy="true">${queue.error ? `Not read: ${esc(queue.error)}` : 'Reading the queue…'}</p>` + workspaceHtml(p);
  const pol = d.policy || {};
  const eff = d.effective || {};
  const state = pol.state === 'unconfigured' ? 'Not configured — nothing is queued or launched'
    : pol.state === 'invalid' ? 'Invalid — grants nothing' : eff.on ? 'On' : 'Off';
  const rows = (d.entries || []).filter(r => ACTIVE.includes(r.stored || r.state));
  const counts = Object.entries(d.counts || {}).map(([k, n]) => `${n} ${esc(k)}`).join(' · ');
  return `<p role="status"><b>${esc(state)}</b>${eff.executes === false ? ' · this installation only reads the queue' : ''}</p>` +
    `<p class="ap-note">These evaluations are launched by the reconciliation policy, not by you. ` +
    `They read only; a proposal waits for your review unless the policy’s apply authority applies it, attributed to the policy.</p>` +
    (counts ? `<p class="ap-note">${counts}</p>` : '') +
    (rows.length ? `<section aria-label="Queue activity"><h3>Activity</h3><ul class="ap-queue">` +
      rows.slice(0, 40).map(r => `<li><a href="/brain/e/${encodeURIComponent(r.entry)}" data-route>${esc(r.title || r.entry)}</a> — ` +
        `<b>${esc(r.label || r.state)}</b>${r.position ? ` · #${r.position} in the queue` : ''}` +
        `${r.why ? `<br><span class="ap-note">${esc(r.why)}</span>` : ''}</li>`).join('') + `</ul></section>`
      : `<p class="ap-note">Nothing is queued, running or waiting.</p>`) +
    workspaceHtml(p) + limitsHtml(p);
}

function registerBrainJobs() {
  registerAgent('brainjob', {
    label: 'Brain Agent',
    capability: 'Drafts from the sources you approved; nothing reaches the Brain until you apply the reviewed batch.',
    key: (t) => `brainjob:${t.draft}`,
    create: () => ({}),
    about: (s) => {
      const d = (jobs[s.target.draft] || {}).data;
      return `${(jobPurpose(s.target, d) || {}).label || 'Brain draft'} ${s.target.draft}`;
    },
    body: brainJobBody,
    opened: (s) => readJob(s.target.draft),
    persist: () => ({}),
    revive: () => ({}),
  });
  registerAgent('reconcile', {
    label: 'Automatic classification',
    capability: 'Observed: evaluations the reconciliation policy queues. It takes no instructions.',
    key: () => 'reconcile',
    create: () => ({}),
    about: () => 'The Brain reconciliation queue',
    body: reconcileBody,
    opened: () => readQueue(),
    persist: () => ({}),
    revive: () => ({}),
  });
}

export { limitsHtml, noComposerHtml, progressBlockHtml, registerBrainJobs, transcriptHtml, workspaceHtml };
