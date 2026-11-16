'use strict';
/* ── the setup guide (task 88) ────────────────────────────────────────── */

/* Six areas read from the same authorities Settings, the doctor and a
   launch ask — `GET /api/setup`, which runs nothing and writes nothing.
   The guide owns no configuration: every "configure" is a link to the
   Settings section that already edits it. Its only writes are a person's
   decision about an area and a proposed setup card, each carrying the
   revision it was read at and a request identity kept until the answer
   arrives, so a double click is one request and a stale page is refused.

   No percentage anywhere: a count per status word, because configured is
   not tested and a deferred area is not done. */
const SETUP_OWNER = { you: 'You', agent: 'An agent, through a card', bench: 'Settings' };

async function fetchSetup(force) {
  if (S.setupFetching) return;
  if (!force && S.setup) return;
  S.setupFetching = true;
  try {
    const res = await fetch('/api/setup');
    const data = await res.json();
    if (res.ok && data.setup) { S.setup = data.setup; S.setupError = null; }
    else S.setupError = data.error || 'the setup guide could not be read';
  } catch (e) {
    S.setupError = e.message || 'the board could not be asked';
  } finally {
    S.setupFetching = false;
    renderSetupChip();
    if (S.view === 'settings' && S.settingsSection === 'setup') renderSettings();
  }
}

/* The invitation. Present while an area needs a decision, is blocked or
   failed; it opens the guide and never stands between a person and the
   board — dismissing it is simply not clicking it. */
function renderSetupChip() {
  const el = $('#setupchip');
  const g = S.setup;
  const open = g ? g.summary.open.length : 0;
  el.hidden = !g || !g.summary.invite;
  if (el.hidden) return;
  el.setAttribute('aria-label', `open the setup guide — ${open} area` +
    `${open === 1 ? '' : 's'} to settle`);
  el.title = 'Setup guide — what was found, what to decide and who does it. ' +
    'Opening it reads; nothing runs.';
  el.innerHTML = `<span class="dot" style="background:var(--idle)"></span>` +
    `<span>◇ set up</span><span class="mono">${open} to settle</span>`;
}

function openSetupGuide() {
  S.settingsSection = 'setup';
  setView('settings');
  fetchSetup(true);
  const h = $('#shead h1');
  if (h) h.focus({ preventScroll: true });
}

function setupFacts(area) {
  if (!area.facts.length) return '';
  return `<dl class="setup-facts">` + area.facts.map(f =>
    `<div><dt>${esc(f.label)}</dt><dd>` +
    (f.local ? `<code>${esc(f.value)}</code>` : esc(f.value)) +
    (f.status ? ' ' + stateWord(f.status, S.setup.statuses) : '') +
    `</dd></div>`).join('') + `</dl>`;
}

function setupNext(area, readOnly) {
  const n = area.next;
  const who = `<span class="sbadge dim">${esc(SETUP_OWNER[n.owner] || n.owner)}</span>`;
  let act = '';
  if (n.kind === 'settings' && n.section) {
    act = `<button class="sb link" data-ssection="${esc(n.section)}" ` +
      `aria-label="${esc(n.label)} (opens ${esc(n.section)} settings)">${esc(n.label)}</button>`;
  } else if (n.kind === 'task' && area.allowed.includes('create-task')) {
    act = `<button class="sb primary" data-setupact="task:${esc(area.id)}" ` +
      `aria-label="${esc(n.label)}"${readOnly ? ' disabled' : ''}>${esc(n.label)}</button>`;
  } else {
    act = `<span>${esc(n.label)}</span>`;
  }
  return `<div class="setup-next"><span class="sname">Next</span>${who}${act}` +
    (n.command ? `<code class="setup-cmd">${esc(n.command)}</code>` : '') +
    (n.detail ? `<p class="shelp">${esc(n.detail)}</p>` : '') + `</div>`;
}

function setupDecision(area, readOnly) {
  const d = area.decision;
  const parts = [];
  if (d) {
    parts.push(`<p class="setup-decided">${stateWord(d.status, S.setup.statuses)} ` +
      `${esc(d.reason || '')} <span class="shelp">— ${esc(d.actor || 'unknown')}, ` +
      `${esc(d.at || '')}</span></p>`);
    if (d.alternative) parts.push(`<p>Verified instead by ` + (d.alternative.check
      ? `the check <code>${esc(d.alternative.check)}</code>`
      : `card <b>#${esc(d.alternative.task)}</b>`) + `.</p>`);
    if (d.stale) parts.push(`<p class="bad">The facts have changed since this ` +
      `was decided. Revisit it: reopen, or decide again.</p>`);
  }
  if (area.task) {
    parts.push(`<p>Setup card <b>#${esc(area.task.number)}</b> — ` +
      `${esc(area.task.title || '')} <span class="shelp">(` +
      `${esc(area.task.stage || 'not found on the board')})</span></p>`);
  }
  const can = area.allowed.filter(a => a !== 'create-task');
  if (can.length && !readOnly) {
    const choices = can.filter(a => a !== 'reopen');
    parts.push(`<div class="setup-decide">` +
      (choices.length || d ? `<label for="setup-reason-${esc(area.id)}">Reason</label>` +
        `<input id="setup-reason-${esc(area.id)}" type="text" maxlength="500" ` +
        `value="${esc((S.setupReasons || {})[area.id] || '')}" ` +
        `placeholder="Why — kept with the decision">` : '') +
      (area.id === 'preview' && (choices.includes('not-applicable') || (d && d.status === 'deferred'))
        ? `<label for="setup-alt-preview">Instead, verified by (a registered check, or a card #)</label>` +
          `<input id="setup-alt-preview" type="text" maxlength="64" ` +
          `value="${esc((S.setupReasons || {})['alt-preview'] || '')}" ` +
          `placeholder="cli-smoke, or #42 — optional">` : '') +
      // a decision already made can be changed to the other one, or reopened
      (d ? [d.status === 'deferred' ? 'not-applicable' : 'defer'].filter(c =>
            c === 'defer' || area.canBeNotApplicable)
         : choices).map(c =>
        `<button class="sb" data-setupact="${esc(c)}:${esc(area.id)}" ` +
        `aria-label="${c === 'defer' ? 'defer' : 'mark not applicable'}: ` +
        `${esc(area.title)}">${c === 'defer' ? 'Defer' : 'Not applicable'}</button>`).join('') +
      (can.includes('reopen') ? `<button class="sb link" data-setupact="reopen:${esc(area.id)}" ` +
        `aria-label="reopen: ${esc(area.title)}">Reopen</button>` : '') +
      `</div>`);
  }
  return parts.join('');
}

function settingsSetup() {
  const g = S.setup;
  if (!g) {
    fetchSetup();
    return `<div class="panel"><p class="${S.setupError ? 'bad' : ''}">` +
      `${esc(S.setupError || 'Reading this project. Nothing is being run.')}</p></div>`;
  }
  const counts = Object.entries(g.summary.counts).filter(([, n]) => n);
  const ro = g.readOnly;
  const head = `<div class="panel setup-summary" role="status">` +
    `<p class="lead">${g.summary.open.length
      ? `<b>${g.summary.open.length} of ${g.areas.length} areas</b> need you or an agent.`
      : 'Nothing here is waiting on a decision.'} ` +
    `The board works normally meanwhile; nothing here is required to use it.</p>` +
    `<p class="setup-counts">` + counts.map(([w, n]) =>
      `<span>${stateWord(w, g.statuses)} <b>${n}</b></span>`).join(' ') + `</p>` +
    `<p class="shelp">${esc(g.summary.noPercentage)}</p>` +
    (ro ? `<p class="bad">${esc(ro)}</p>` : '') +
    (g.summary.stale.length ? `<p class="bad">Stale decisions: ` +
      `${esc(g.summary.stale.join(', '))} — the facts changed after they were made.</p>` : '') +
    `<div class="acts"><button class="sb link" data-setupact="reread:all" ` +
    `aria-label="re-read the setup guide">Re-read</button></div></div>`;
  const interrupted = g.interrupted.length
    ? `<div class="panel"><h2>Interrupted</h2>` + g.interrupted.map(op =>
        `<p>${esc(op.kind)} on ${esc(op.area)}, started ${esc(op.at || '')}. ` +
        `${esc(op.recovery)}</p>` + (op.kind === 'task'
          ? `<div class="acts"><button class="sb" data-setupact="resume:${esc(op.request)}" ` +
            `data-sname="${esc(op.area)}" aria-label="resume the interrupted ` +
            `${esc(op.kind)} on ${esc(op.area)}">Resume</button></div>` : '')).join('') +
      `</div>` : '';
  const h = g.handoff || {};
  const handoff = h.present
    ? sref(`From the terminal setup${h.at ? ' · ' + h.at : ''}`,
        `<p class="shelp">${esc(h.historical || h.problem || '')}</p>` +
        (h.guide || []).map(i => `<p><b>${esc(i.title)}</b>` +
          (i.area ? ` <span class="sbadge dim">${esc(i.area)}</span>` : '') +
          `<br><span class="shelp">${esc(i.how)}</span></p>`).join('') +
        (h.failed || []).map(o => `<p class="bad">${esc(o.summary || o.id)}: failed</p>`).join(''))
    : '';
  const areas = g.areas.map(a =>
    `<section class="panel setup-area" id="setup-${esc(a.id)}" aria-labelledby="setup-h-${esc(a.id)}">` +
    `<div class="setup-ahead"><h2 id="setup-h-${esc(a.id)}">${esc(a.title)}</h2>` +
    stateWord(a.status, g.statuses) +
    (a.status !== a.computed ? ` <span class="shelp">read now: ${esc(a.computed)}</span>` : '') +
    `</div>` +
    (a.problem ? `<p class="bad">${esc(a.problem)}</p>` : '') +
    setupFacts(a) +
    (a.recommendation ? `<p><b>Recommended.</b> ${esc(a.recommendation)}</p>` : '') +
    (a.consequence ? `<p class="shelp">${esc(a.consequence)}</p>` : '') +
    setupNext(a, ro) + setupDecision(a, ro) + `</section>`).join('');
  const store = sref('Where this is kept, and what it shares',
    `<p>${esc(g.storage.portable)}.</p><p>${esc(g.storage.local)}.</p>` +
    `<p>Contract <code>${esc(g.kind)} v${esc(g.v)}</code>, revision ` +
    `<code>${esc(g.revision)}</code>. ${esc(g.endpoints.read)}, ` +
    `${esc(g.endpoints.decide)}, ${esc(g.endpoints.task)}.</p>`);
  return head + interrupted + handoff + setupCards(g, ro) +
    setupReadiness(g.verificationReadiness, ro) +
    setupPreviewLife(g.applicationPreview, ro) + areas + store;
}

